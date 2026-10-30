"""VideoEditor launcher for the Windows installer.

The Start menu shortcut runs this file with the bundled pythonw.exe (no console window):

    {app}\\python\\pythonw.exe {app}\\app\\launcher.py          open the Studio
    {app}\\python\\pythonw.exe {app}\\app\\launcher.py --stop   close it

If the Studio is already running it only opens the browser. Output goes to a log file in
the user's data folder, because pythonw has no console to print to.
"""
import json
import os
import socket
import sys
import urllib.request
import webbrowser
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
HOST = '127.0.0.1'
PORTS = range(8765, 8775)


def _environment():
    os.environ.setdefault('VIDEOEDITOR_INSTALLED', '1')
    ffmpeg = ROOT / 'ffmpeg' / 'bin'
    if ffmpeg.is_dir():
        os.environ['PATH'] = f"{ffmpeg}{os.pathsep}{os.environ.get('PATH', '')}"
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))


def _ours(port):
    try:
        with urllib.request.urlopen(f'http://{HOST}:{port}/api/system/health', timeout=1.5) as response:
            return json.loads(response.read()).get('app') == 'videoeditor'
    except (OSError, ValueError):
        return False


def _free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex((HOST, port)) != 0


def _running():
    return next((port for port in PORTS if _ours(port)), None)


def _stop(port):
    request = urllib.request.Request(
        f'http://{HOST}:{port}/api/system/shutdown', method='POST',
        headers={'Origin': f'http://{HOST}:{port}'})
    try:
        urllib.request.urlopen(request, timeout=3).close()
    except OSError:
        pass


def _log():
    from video_editor.settings import DATA_DIR
    folder = DATA_DIR / 'logs'
    folder.mkdir(parents=True, exist_ok=True)
    stream = open(folder / 'studio.log', 'a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = stream
    return stream


def main(argv):
    _environment()
    port = _running()
    if '--stop' in argv:
        if port:
            _stop(port)
        return 0
    if port:
        if '--no-browser' not in argv:
            webbrowser.open(f'http://{HOST}:{port}/')
        return 0
    port = next((p for p in PORTS if _free(p)), None)
    if port is None:
        return 1
    # Settings read the port at import time, so it is chosen before anything is imported.
    os.environ['SERVER_HOST'], os.environ['SERVER_PORT'] = HOST, str(port)
    _log()
    print(f'--- VideoEditor starting on port {port}', flush=True)
    import cli
    cli.serve(open_browser='--no-browser' not in argv)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
