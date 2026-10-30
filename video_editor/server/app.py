"""Minimal local application. Legacy engines have no imports in this entry point."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from starlette.middleware.trustedhost import TrustedHostMiddleware
from video_editor.settings import BASE_DIR, JOBS_DIR, LOCAL_CONFIG_DIR, SOURCE_VIDEOS_DIR
from video_editor import storage
from video_editor.jobs import Jobs
from video_editor.worker import Worker, BusyError
from video_editor.pipeline import Pipeline
from video_editor.export.service import Exports
from video_editor.automatic import Automatic
from video_editor.editing import RevisionConflict
from video_editor.core.job_lock import JobLockTimeoutError
from video_editor.server import jobs_api, editing_api, editorial_api


def create_app(jobs_dir=JOBS_DIR, source_dir=SOURCE_VIDEOS_DIR, pipeline=None, renderer=None, recover=True, editorial_config=None):
    jobs = Jobs(jobs_dir, source_dir)
    worker = Worker()
    preparation = pipeline or Pipeline(jobs)
    exports = Exports(jobs, worker, **({'render_function': renderer} if renderer else {}))
    provider_config=editorial_config or LOCAL_CONFIG_DIR/'editorial_provider.local.json'
    automatic=Automatic(jobs,worker,preparation,exports,provider_config)

    @asynccontextmanager
    async def lifespan(application):
        from starlette.concurrency import run_in_threadpool
        instance = jobs.runtime_lock()
        await run_in_threadpool(instance.__enter__)
        try:
            await run_in_threadpool(recover_tasks)
            yield
        finally:
            await run_in_threadpool(worker.close)
            from video_editor.transcription import release_model
            await run_in_threadpool(release_model)
            await run_in_threadpool(instance.__exit__, None, None, None)

    def recover_tasks():
        if recover:
            pending_automatic=[]
            for job in jobs.list():
                if job.get('legacy'):
                    continue
                job_id = job['job_id']
                if job['state'] in ('queued', 'processing'):
                    jobs.update(job_id, state='interrupted', error='Processo interrompido; retome explicitamente.')
                for export in exports.list(job_id):
                    if export['state'] in ('queued', 'rendering'):
                        export.update(state='failed', error='Exportação interrompida; solicite outra.')
                        jobs.write(job_id, f'exports/{export["render_id"]}/export.json', export)
                path = jobs.path(job_id, 'editorial/status.json')
                if path.exists() and storage.read(path).get('state') == 'queued':
                    jobs.write(job_id, 'editorial/status.json', {'state': 'manual', 'error': 'Assistência interrompida.'})
                path=jobs.path(job_id,'automatic.json')
                if path.exists():
                    state=storage.read(path)
                    from video_editor.automatic import ACTIVE_STATES
                    if state['state'] in ACTIVE_STATES:
                        state.update(state='interrupted',error='Edição interrompida; retome explicitamente.')
                        jobs.write(job_id,'automatic.json',state)
                        pending_automatic.append(job_id)
            # Dispatch only after scanning old export states; do not mark a newly
            # resumed renderer interrupted during the same recovery scan.
            for job_id in pending_automatic:
                try:
                    automatic.recover(job_id)
                except (ValueError,OSError,KeyError):
                    pass  # Keep incompatible/user-modified jobs interrupted.
    application = FastAPI(title='VideoEditor Minimal V1', lifespan=lifespan)
    application.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','[::1]','testserver'])
    application.state.jobs, application.state.worker = jobs, worker
    application.state.pipeline, application.state.exports = preparation, exports
    application.state.automatic=automatic

    @application.middleware('http')
    async def local_origin(request: Request, call_next):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            base_url = str(request.base_url).rstrip('/')
            sec_fetch_site = request.headers.get('sec-fetch-site')
            origin = request.headers.get('origin')
            referer = request.headers.get('referer')
            if sec_fetch_site == 'cross-site':
                return JSONResponse({'detail': 'Origem externa bloqueada.'}, status_code=403)
            if origin and origin != base_url:
                return JSONResponse({'detail': 'Origem externa bloqueada.'}, status_code=403)
            if not origin and referer and not referer.startswith(base_url):
                return JSONResponse({'detail': 'Origem externa bloqueada.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # ES modules are imported without a version query; revalidate them (ETag) so an
        # update never runs next to a stale module from the heuristic cache.
        path = request.url.path
        if path.startswith('/assets/') or path in ('/', '/studio.html', '/editor.html', '/review.html'):
            response.headers.setdefault('Cache-Control', 'no-cache')
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @application.exception_handler(RevisionConflict)
    async def conflict(request, error):
        return JSONResponse({'detail': str(error)}, status_code=409)

    @application.exception_handler(JobLockTimeoutError)
    async def lock_timeout(request, error):
        return JSONResponse({'detail': str(error)}, status_code=409)

    @application.exception_handler(BusyError)
    async def busy(request, error):
        return JSONResponse({'detail': str(error)}, status_code=429)

    @application.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({'detail': str(error)}, status_code=422)

    @application.exception_handler(PermissionError)
    async def forbidden(request, error):
        return JSONResponse({'detail': str(error)}, status_code=403)

    @application.exception_handler(FileNotFoundError)
    async def missing(request, error):
        return JSONResponse({'detail': str(error)}, status_code=404)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        if request.url.path == '/api/editorial/settings':
            return JSONResponse({'detail': 'Configuração inválida. Confira a chave e as confirmações.'}, status_code=422)
        return await request_validation_exception_handler(request, error)

    application.include_router(jobs_api.router(jobs, worker, preparation, exports,automatic))
    application.include_router(editing_api.router(jobs, exports))
    from video_editor.server import system_api
    application.include_router(system_api.router())
    from video_editor.productivity import router as productivity_router
    application.include_router(productivity_router(jobs, exports))
    application.include_router(editorial_api.router(jobs, worker, provider_config))
    from video_editor.editorial.settings import router as settings_router
    application.include_router(settings_router(provider_config))

    ui = BASE_DIR / 'ui/minimal'
    application.mount('/assets', StaticFiles(directory=ui), name='assets')

    @application.get('/')
    @application.get('/studio.html')
    def studio():
        return FileResponse(ui / 'studio.html')

    @application.get('/editor.html')
    @application.get('/review.html')
    def editor():
        return FileResponse(ui / 'editor.html')

    return application


app = create_app()
