"""CLI and HTTP share the same job, preparation and export services."""
import argparse
import sys
import threading
import webbrowser
from pathlib import Path
from video_editor.settings import SERVER_HOST, SERVER_PORT
from video_editor.models import PrepareConfig
from video_editor.server.app import app


def serve(open_browser=True, job_id=None):
    url = f'http://{SERVER_HOST}:{SERVER_PORT}/' + (f'editor.html?job_id={job_id}' if job_id else '')
    print(url)
    if open_browser:
        threading.Timer(1, lambda: webbrowser.open(url)).start()
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, workers=1)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='VideoEditor local — Minimal V1')
    sub = parser.add_subparsers(dest='command', required=True)
    studio = sub.add_parser('studio')
    studio.add_argument('--no-browser', action='store_true')
    sub.add_parser('list-videos')
    download = sub.add_parser('download-model', help='Baixa somente pesos gratuitos do Whisper, sem enviar mídia')
    download.add_argument('--model',default='large-v3-turbo',choices=['tiny','base','small','medium','large-v3-turbo','large-v3'])
    process = sub.add_parser('process')
    process.add_argument('video', help='Caminho explícito da gravação MP4, MOV ou MKV')
    process.add_argument('--model', default='large-v3-turbo', choices=['tiny', 'base', 'small', 'medium', 'large-v3-turbo', 'large-v3'])
    process.add_argument('--device', default='cuda', choices=['cuda', 'cpu'])
    process.add_argument('--compute-type', default='int8_float16', choices=['int8_float16', 'int8', 'float16', 'float32'])
    process.add_argument('--render-now', action='store_true')
    process.add_argument('--no-browser', action='store_true')
    process.add_argument('--automatic',action='store_true',help='Limpa e exporta MP4 + SRT automaticamente')
    process.add_argument('--evaluation','--experimental',action='store_true',help='Aplica cortes locais experimentais (retakes, falsos começos e pausas)')
    process.add_argument('--send-text',action='store_true',help='Autoriza somente texto ao Gemini gratuito configurado')
    calibrate = sub.add_parser('calibrate',help='Mede lotes de transcrição no seu PC sem editar o raw')
    calibrate.add_argument('video')
    calibrate.add_argument('--seconds',type=float,default=60)
    migrate = sub.add_parser('migrate')
    migrate.add_argument('job_id')
    export = sub.add_parser('export')
    export.add_argument('job_id')
    export.add_argument('--revision', type=int, required=True)
    report = sub.add_parser('report', help='Métricas de naturalidade da edição atual, somente leitura')
    report.add_argument('job_id')
    report.add_argument('--json', action='store_true')
    ledger = sub.add_parser('ledger', help='Registro de decisões humanas e replay da política atual')
    ledger_sub = ledger.add_subparsers(dest='ledger_command', required=True)
    ledger_sub.add_parser('build')
    ledger_replay = ledger_sub.add_parser('replay')
    ledger_replay.add_argument('--json', action='store_true')
    glossary = sub.add_parser('glossary', help='Glossário de nomes: OCR dos slides e correção conservadora')
    glossary_sub = glossary.add_subparsers(dest='glossary_command', required=True)
    glossary_scan = glossary_sub.add_parser('scan')
    glossary_scan.add_argument('job_id')
    glossary_apply = glossary_sub.add_parser('apply')
    glossary_apply.add_argument('job_id')
    glossary_apply.add_argument('--dry-run', action='store_true')
    voice = sub.add_parser('voice', help='Detector de voz de fundo (locutor diferente do criador)')
    voice_sub = voice.add_subparsers(dest='voice_command', required=True)
    voice_scan = voice_sub.add_parser('scan')
    voice_scan.add_argument('job_id')
    voice_scan.add_argument('--json', action='store_true')
    args = parser.parse_args()
    jobs, worker = app.state.jobs, app.state.worker
    if args.command == 'studio':
        serve(not args.no_browser)
    elif args.command == 'list-videos':
        for source in jobs.sources():
            print(source['path'])
    elif args.command == 'download-model':
        from faster_whisper.utils import download_model
        print(download_model(args.model))
    elif args.command == 'report':
        import json
        from video_editor import report as edit_report
        # Read-only: safe while the Studio is running, so no runtime lock.
        value = edit_report.build(jobs, args.job_id, app.state.exports)
        print(json.dumps(value, indent=2, ensure_ascii=False) if args.json else edit_report.text(value))
    elif args.command == 'migrate':
        print(jobs.migrate(args.job_id)['job_id'])
    elif args.command == 'ledger':
        import json
        from video_editor import decision_ledger
        if args.ledger_command == 'build':
            result = decision_ledger.build(jobs)
            print(
                f"{result['examples']} exemplo(s) de {result['jobs']} job(s) "
                f"gravados em {result['path']}"
            )
        else:
            try:
                examples = decision_ledger.load()
            except FileNotFoundError:
                parser.error('Rode python cli.py ledger build primeiro.')
            value = decision_ledger.replay(examples)
            print(
                json.dumps(value, ensure_ascii=False, indent=2)
                if args.json
                else decision_ledger.text(value)
            )
    elif args.command == 'glossary':
        from video_editor import glossary
        from video_editor.timeline import digest
        job = jobs.info(args.job_id)
        duration = float(job['media']['duration'])
        cache = jobs.path(args.job_id, 'visual/screen_terms.json')
        if args.glossary_command == 'scan':
            value = glossary.screen_terms(
                job['source']['path'], duration, cache_path=cache
            )
            print(
                f"OCR: {value['state']} "
                f"({value.get('frames', 0)} quadros; cache: {cache})"
            )
            print('Termos da tela:', ', '.join(value.get('terms') or []) or '(nenhum)')
            user = glossary.user_terms()
            if user:
                print('Glossário do usuário:', ', '.join(user))
            hints = glossary.hotwords(user + list(value.get('terms') or []))
            print('Hotwords:', hints or '(vazio)')
        else:
            transcript = jobs.read(args.job_id, 'transcript.json')
            terms = glossary.user_terms()
            if not terms:
                parser.error('Nenhum termo em config/glossary.local.json.')
            corrected, changes = glossary.correct(transcript, terms)
            rejected = glossary.skipped(transcript, terms)
            for change in changes:
                print(
                    f"{change['start']:.2f}-{change['end']:.2f}s "
                    f"{change['word_id']}: \"{change['before']}\" -> "
                    f"\"{change['after']}\" (termo: {change['term']})"
                )
            for item in rejected:
                print(
                    f"não aplicado (proteção): \"{item['text']}\" ~ "
                    f"\"{item['term']}\" (prob={item['probability']:.3f})"
                )
            if args.dry_run:
                print(f"{len(changes)} troca(s); dry-run, nada foi gravado.")
            else:
                backup = jobs.path(args.job_id, 'transcript.raw.json')
                if not backup.exists():
                    jobs.write(args.job_id, 'transcript.raw.json', transcript)
                jobs.write(args.job_id, 'transcript.json', corrected)
                edit_path = jobs.path(args.job_id, 'edit.json')
                if edit_path.exists():
                    edit = jobs.read(args.job_id, 'edit.json')
                    if edit.get('transcript_hash') != digest(corrected):
                        edit['transcript_hash'] = digest(corrected)
                        jobs.write(args.job_id, 'edit.json', edit)
                print(
                    f"{len(changes)} troca(s) gravada(s); backup: {backup}"
                )
    elif args.command == 'voice':
        import json
        from video_editor import voice_presence
        job = jobs.info(args.job_id)
        transcript = jobs.read(args.job_id, 'transcript.json')
        presence_path = jobs.path(args.job_id, 'visual/presence.json')
        presence = (
            jobs.read(args.job_id, 'visual/presence.json')
            if presence_path.exists()
            else None
        )
        value = voice_presence.analyze(
            jobs.path(args.job_id, 'audio/extracted_16k.wav'),
            transcript,
            float(job['media']['duration']),
            job['source'],
            presence=presence,
            cache_path=jobs.path(args.job_id, 'voice/voice_presence.json'),
        )
        print(
            json.dumps(value, ensure_ascii=False, indent=2)
            if args.json
            else voice_presence.text(value)
        )
    elif args.command == 'export':
        with jobs.runtime_lock():
            item = app.state.exports.request(args.job_id, args.revision)
            worker.queue.join()
            worker.close()
            print(app.state.exports.get(args.job_id, item['render_id']))
    elif args.command == 'calibrate':
        import tempfile
        from video_editor import media
        from video_editor.performance import calibrate as measure
        from video_editor.safety import source_identity
        from video_editor.settings import DATA_DIR, LOCAL_CONFIG_DIR
        if not 5<=args.seconds<=300:parser.error('--seconds deve estar entre 5 e 300')
        source=Path(args.video).resolve()
        (DATA_DIR/'scratch').mkdir(exist_ok=True)
        with jobs.runtime_lock(),tempfile.TemporaryDirectory(prefix='asr-calibration-',dir=DATA_DIR/'scratch') as folder:
            identity=source_identity(source);info=media.probe(source);wav=Path(folder)/'sample.wav'
            seek=['-ss',str(info['origin'])] if info['origin']>0 else []
            media.run(['ffmpeg','-nostdin','-y','-v','error',*seek,'-i',str(source),'-vn',
                       '-af','aresample=async=1:first_pts=0','-t',str(args.seconds),
                       '-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)])
            result=measure(wav,identity,LOCAL_CONFIG_DIR/'asr_calibration.local.json')
            print('Lote selecionado:',result['selected_batch_size'])
            print('Relatório:',LOCAL_CONFIG_DIR/'asr_calibration.local.json')
        from video_editor.transcription import release_model
        release_model()
    elif args.command == 'process':
        if args.send_text and not args.automatic:parser.error('--send-text exige --automatic')
        config = PrepareConfig(model=args.model, device=args.device, compute_type=args.compute_type)
        with jobs.runtime_lock():
            if args.automatic and config.model == 'small':
                # ASR batching was calibrated for the small model only.
                from video_editor.performance import selected_batch
                from video_editor.settings import DATA_DIR, LOCAL_CONFIG_DIR
                config.batch_size=selected_batch(LOCAL_CONFIG_DIR/'asr_calibration.local.json')
            if args.automatic:
                config.refine_uncertain=True
            job = jobs.create(Path(args.video).resolve(), config.model_dump(), allow_external=True)
            if args.automatic:
                mode = 'evaluation' if args.evaluation else 'standard'
                app.state.automatic.request(job['job_id'],dict(send_text=args.send_text,export=True,mode=mode))
                worker.queue.join();worker.close()
                print(app.state.automatic.status(job['job_id']))
                return
            worker.submit(app.state.pipeline.prepare, job['job_id']).result()
            if args.render_now:
                item = app.state.exports.request(job['job_id'], 0)
                worker.queue.join()
                print(app.state.exports.get(job['job_id'], item['render_id']))
                worker.close()
        if not args.render_now:
            serve(not args.no_browser, job['job_id'])


if __name__ == '__main__':
    main()
