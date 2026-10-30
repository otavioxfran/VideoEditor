"""One bounded worker per local app; tasks never run on the ASGI event loop."""
import logging
from concurrent.futures import Future
from queue import Queue, Empty, Full
from threading import Thread, Lock


class BusyError(ValueError):
    pass


class Worker:
    def __init__(self, capacity=8):
        self.queue = Queue(maxsize=capacity)
        self.thread = None
        self._start_lock = Lock()
        self._closing = False

    def start(self):
        with self._start_lock:
            if self.thread is None and not self._closing:
                self.thread = Thread(target=self._run, name='video-editor-worker', daemon=True)
                self.thread.start()

    def submit(self, function, *args):
        with self._start_lock:
            if self._closing:
                raise BusyError('Worker encerrando; inicie outra sessão.')
            future = Future()
            try:
                self.queue.put_nowait((future, function, args))
            except Full:
                raise BusyError("Fila local cheia. Aguarde uma tarefa terminar.") from None
            if self.thread is None:
                self.thread = Thread(target=self._run, name='video-editor-worker', daemon=True)
                self.thread.start()
            return future

    def _run(self):
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    return
                future, function, args = item
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(function(*args))
                    except BaseException as error:
                        future.set_exception(error)
                        logging.getLogger(__name__).exception('Tarefa local falhou')
            finally:
                self.queue.task_done()

    def close(self, timeout=10.0):
        # Finish accepted tasks before shutdown; no partial operation is silently abandoned.
        with self._start_lock:
            if self._closing:
                return
            self._closing = True
            thread = self.thread
        if thread is not None:
            try:
                self.queue.put(None, timeout=2.0)
            except Full:
                pass
            thread.join(timeout=timeout)
            if thread.is_alive():
                logging.getLogger(__name__).warning("Worker thread did not terminate within %s seconds", timeout)
            with self._start_lock:
                self.thread = None
