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
    finish = sub.add_parser('finish', help='Acabamento: overlays, cards, tarjas, CapCut e render')
    finish_sub = finish.add_subparsers(dest='finish_command', required=True)
    finish_render = finish_sub.add_parser('render')
    finish_render.add_argument('job_id')
    finish_render.add_argument('--plan', required=True)
    finish_render.add_argument('--base')
    finish_render.add_argument('--output')
    audio_group = finish_render.add_mutually_exclusive_group()
    audio_group.add_argument('--master-audio', action='store_true')
    audio_group.add_argument('--no-master-audio', action='store_true')
    finish_capcut = finish_sub.add_parser('capcut')
    finish_capcut.add_argument('job_id')
    finish_capcut.add_argument('--plan', required=True)
    finish_capcut.add_argument('--template', required=True)
    finish_capcut.add_argument('--target')
    finish_capcut.add_argument('--name')
    finish_fetch = finish_sub.add_parser('fetch-images')
    finish_fetch.add_argument('--query', action='append', required=True)
    finish_fetch.add_argument('--out')
    finish_audio = finish_sub.add_parser('audio', help='Analisar e masterizar áudio de voz')
    audio_sub = finish_audio.add_subparsers(dest='audio_command', required=True)
    audio_analyze = audio_sub.add_parser('analyze')
    audio_analyze.add_argument('video')
    audio_analyze.add_argument('--json', action='store_true')
    audio_master = audio_sub.add_parser('master')
    audio_master.add_argument('video')
    audio_master.add_argument('--output', required=True)
    audio_master.add_argument('--target', type=float, default=-14)
    audio_master.add_argument('--denoise', choices=('auto', 'on', 'off'), default='auto')
    finish_suggest = finish_sub.add_parser('suggest')
    finish_suggest.add_argument('job_id')
    finish_suggest.add_argument('--export')
    finish_review = finish_sub.add_parser('review')
    finish_review.add_argument('job_id')
    finish_decide = finish_sub.add_parser('decide')
    finish_decide.add_argument('job_id')
    finish_decide.add_argument('item_id')
    finish_decide.add_argument('action', choices=('accept', 'reject'))
    finish_plan = finish_sub.add_parser('plan')
    finish_plan.add_argument('job_id')
    finish_plan.add_argument('--out')
    finish_plan.add_argument('--accept-all', action='store_true')
    finish_describe = finish_sub.add_parser('describe')
    finish_describe.add_argument('job_id')
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
    elif args.command == 'finish':
        import json
        from video_editor.finishing import assets, capcut, models
        from video_editor.finishing import render as finishing_render
        from video_editor.finishing import suggest
        if args.finish_command == 'audio':
            from video_editor.finishing import audio
            if args.audio_command == 'analyze':
                value = audio.analyze(args.video)
                print(
                    json.dumps(value, ensure_ascii=False, indent=2)
                    if args.json
                    else value['report']
                )
            else:
                value = audio.master(
                    args.video, args.output,
                    options={'target_lufs': args.target, 'denoise': args.denoise},
                )
                print(value['after_report'])
                print('Filtros aplicados:')
                for item in value['filters']:
                    print(f"- {item['filter']}: {item['reason']}")
        elif args.finish_command == 'fetch-images':
            from video_editor.settings import DATA_DIR
            out = Path(args.out) if args.out else DATA_DIR / 'scratch' / 'finishing-images'
            records = assets.fetch_images(args.query, out)
            print(f'{len(records)} imagem(ns) em {out}')
            for record in records:
                print(record['path'], record['license'])
        elif args.finish_command == 'suggest':
            value = suggest.suggest(jobs, args.job_id, args.export)
            counts = {}
            for item in value['items']:
                counts[item['kind']] = counts.get(item['kind'], 0) + 1
            print(f"revisão {value['revision']}: {len(value['items'])} sugestão(ões)")
            for kind, count in sorted(counts.items()):
                print(f'- {kind}: {count}')
        elif args.finish_command == 'review':
            value = suggest.load_suggestions(jobs, args.job_id)
            print(f"revisão {value['revision']}: {len(value['items'])} sugestão(ões)")
            for index, item in enumerate(value['items'], 1):
                print(
                    f"{index}. [{item['start']:.2f}-{item['end']:.2f}] "
                    f"{item['kind']} | {item['reason']} | {item['status']}"
                    + (' | needs_text' if item.get('needs_text') else '')
                )
        elif args.finish_command == 'decide':
            value = suggest.load_suggestions(jobs, args.job_id)
            value = suggest.decide(jobs, args.job_id, dict(
                expected_revision=value['revision'], item_id=args.item_id,
                action=args.action,
            ))
            print(f"revisão {value['revision']}")
        elif args.finish_command == 'plan':
            value = suggest.build_plan(
                jobs, args.job_id, out=args.out, accept_all=args.accept_all
            )
            print(value['path'])
        elif args.finish_command == 'describe':
            value = suggest.describe(jobs, args.job_id)
            if value['text'].strip():
                print(value['text'])
            else:
                print('Descrição vazia: gere o briefing editorial (títulos, resumo e capítulos) '
                      'no Studio e aceite sugestões com créditos antes de descrever.')
        else:
            jobs.info(args.job_id)
            plan = models.load_plan(args.plan)
            if args.finish_command == 'render':
                master = True if args.master_audio else False if args.no_master_audio else None
                if args.base:
                    base = Path(args.base)
                else:
                    exports = jobs.path(args.job_id, 'exports')
                    candidates = []
                    if exports.is_dir():
                        for directory in exports.iterdir():
                            video = directory / 'video.mp4'
                            record = directory / 'export.json'
                            if not video.is_file():
                                continue
                            complete = True
                            if record.is_file():
                                try:
                                    complete = json.loads(
                                        record.read_text(encoding='utf-8')
                                    ).get('state') == 'complete'
                                except (OSError, ValueError):
                                    complete = False
                            if complete:
                                candidates.append(video)
                    if not candidates:
                        parser.error('Nenhum export completo; informe --base.')
                    base = max(candidates, key=lambda item: item.stat().st_mtime)
                output = Path(args.output) if args.output else base.with_name(base.stem + '-final.mp4')
                result = finishing_render.render(
                    base, plan, output, master_audio=master
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                result = capcut.export_draft(
                    args.template, plan, target=args.target, name=args.name
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
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
        if not 5 <= args.seconds <= 300:
            parser.error('--seconds deve estar entre 5 e 300')
        source=Path(args.video).resolve()
        (DATA_DIR/'scratch').mkdir(exist_ok=True)
        with jobs.runtime_lock(),tempfile.TemporaryDirectory(prefix='asr-calibration-',dir=DATA_DIR/'scratch') as folder:
            identity = source_identity(source)
            info = media.probe(source)
            wav = Path(folder) / 'sample.wav'
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
        if args.send_text and not args.automatic:
            parser.error('--send-text exige --automatic')
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
                worker.queue.join()
                worker.close()
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
