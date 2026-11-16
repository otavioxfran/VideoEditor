"""Screen OCR glossary, ASR hotwords and conservative transcript text repair."""
import copy
import json
import re
import subprocess
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from video_editor import media, storage
from video_editor.settings import BASE_DIR

VERSION = 6
SAMPLE_SECONDS = 10.0
MAX_FRAMES = 240
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
MIN_FRAMES = 2
MIN_SHARE = .15
MAX_TERMS = 40
MAX_HOTWORD_CHARS = 800
MAX_HOTWORD_TOKENS = 200
USER_GLOSSARY = BASE_DIR / 'config' / 'glossary.local.json'
EXAMPLE_GLOSSARY = BASE_DIR / 'config' / 'glossary.example.json'
COMMON_WORDS = {
    'a', 'o', 'as', 'os', 'um', 'uma', 'uns', 'umas', 'de', 'do', 'da', 'dos', 'das',
    'em', 'no', 'na', 'nos', 'nas', 'por', 'para', 'pra', 'com', 'sem', 'que', 'se',
    'e', 'ou', 'mas', 'como', 'quando', 'onde', 'porque', 'sobre', 'entre', 'ate',
    'ja', 'nao', 'sim', 'mais', 'menos', 'muito', 'muita', 'muitos', 'muitas',
    'pouco', 'pouca', 'poucos', 'poucas', 'todo', 'toda', 'todos', 'todas',
    'outro', 'outra', 'outros', 'outras', 'mesmo', 'mesma', 'ser', 'estar', 'ter',
    'haver', 'fazer', 'dizer', 'poder', 'querer', 'ficar', 'ir', 'vir', 'ver',
    'dar', 'saber', 'ele', 'ela', 'eles', 'elas', 'eu', 'tu', 'voce', 'voces',
    'lhe', 'lhes', 'meu', 'minha', 'seu', 'sua', 'nosso', 'nossa', 'este', 'esta',
    'esse', 'essa', 'aquele', 'aquela', 'isto', 'isso', 'aquilo', 'aqui', 'ali',
    'la', 'agora', 'entao', 'tambem', 'ainda', 'bem', 'mal', 'so', 'cada', 'qual',
    'quais', 'quem', 'cujo', 'assim', 'apos', 'antes', 'depois', 'durante',
    'contra', 'desde', 'sob', 'tras', 'frente', 'perto', 'longe', 'dentro', 'fora',
    'cima', 'baixo', 'anos', 'ano', 'mes', 'meses', 'dia', 'dias', 'hora', 'horas',
    'minuto', 'minutos', 'segundo', 'segundos', 'vez', 'vezes', 'parte', 'partes',
    'coisa', 'coisas', 'gente', 'pessoa', 'pessoas', 'homem', 'homens', 'mulher',
    'mulheres', 'crianca', 'criancas', 'vida', 'mundo', 'pais', 'paises', 'cidade',
    'trabalho', 'empresa', 'empresas', 'dinheiro', 'historia', 'caso', 'casos',
    'forma', 'formas', 'modo', 'tempo', 'momento', 'lugar', 'agua', 'terra',
    'terras', 'fogo', 'ar', 'sol', 'lua', 'noite', 'tarde', 'manha', 'semana',
    'semanas', 'familia', 'filho', 'filha', 'filhos', 'pai', 'mae', 'amigo',
    'amigos', 'nome', 'nomes', 'palavra', 'palavras', 'pergunta', 'resposta',
    'ideia', 'ideias', 'problema', 'problemas', 'exemplo', 'exemplos', 'grupo',
    'grupos', 'numero', 'numeros', 'livro', 'livros', 'filme', 'filmes', 'video',
    'videos', 'jogo', 'jogos', 'carro', 'carros', 'casa', 'casas', 'porta',
    'portas', 'mesa', 'mesas', 'cadeira', 'cadeiras', 'couro', 'capitulo',
    'capitulos', 'casamento', 'chinesa', 'chinesas', 'chines', 'chineses',
    'anterior', 'anteriores', 'proximo', 'proxima', 'proximos', 'proximas',
    'ultimo', 'ultima', 'primeiro', 'primeira', 'terceiro', 'terceira', 'novo',
    'nova', 'velho', 'velha', 'bom', 'boa', 'grande', 'grandes', 'pequeno',
    'pequena', 'alto', 'alta', 'longo', 'longa', 'curto', 'curta', 'certo',
    'certa', 'errado', 'errada', 'melhor', 'pior', 'maior', 'menor', 'proprio',
    'propria', 'unico', 'unica', 'varios', 'varias', 'alguns', 'algumas',
    'nenhum', 'nenhuma', 'tudo', 'nada', 'alguem', 'ninguem', 'algo', 'sempre',
    'nunca', 'talvez', 'quase', 'apenas', 'hoje', 'ontem', 'amanha', 'ok', 'etc',
    'porem', 'contudo', 'embora', 'enquanto', 'afinal', 'alias', 'investigacao',
    'investigacoes', 'espionagem', 'desvio', 'confissao', 'traicao', 'trilhao',
    'trilhoes', 'bilhao', 'bilhoes', 'milhao', 'milhoes', 'desesperada',
    'desesperado', 'chips', 'chip', 'market', 'cap', 'ia', 'conjugal', 'dossie',
    'escandalo', 'marido', 'esposa', 'amante', 'bilionario', 'tecnologia',
    'governo', 'guerra', 'economia', 'semicondutores', 'raras', 'bloqueio',
    'sancoes', 'acoes', 'bolsa', 'investidores', 'preco', 'mercado', 'the', 'an',
    'and', 'or', 'but', 'of', 'to', 'in', 'on', 'at', 'for', 'with', 'from', 'by',
    'is', 'are', 'was', 'were', 'be', 'been', 'being', 'this', 'that', 'these',
    'those', 'it', 'its', 'we', 'you', 'they', 'he', 'she', 'i', 'not', 'no',
    'yes', 'new', 'old', 'more', 'most', 'all', 'any', 'some', 'can', 'will',
    'just', 'year', 'years', 'month', 'months', 'day', 'days', 'time', 'life',
    'world', 'company', 'companies', 'money', 'history', 'case', 'form', 'way',
    'place', 'water', 'land', 'family', 'son', 'daughter', 'friend', 'name',
    'word', 'question', 'answer', 'idea', 'problem', 'example', 'group', 'number',
    'book', 'movie', 'video', 'game', 'car', 'house', 'door', 'table', 'chair',
    'leather', 'chapter', 'marriage', 'chinese', 'previous', 'next', 'last',
    'first', 'second', 'third', 'good', 'great', 'small', 'high', 'low', 'long',
    'short', 'right', 'wrong', 'better', 'worse', 'only', 'several', 'few', 'none',
    'nothing', 'everything', 'someone', 'nobody', 'something', 'here', 'there',
    'now', 'always', 'never', 'maybe', 'almost', 'then', 'before', 'after',
    'today', 'yesterday', 'tomorrow', 'woman', 'women', 'men', 'man',
    'pelo', 'pela', 'pelos', 'pelas', 'internacional', 'jaqueta', 'jaquetas',
    'suposta', 'suposto', 'espia', 'nov', 'futuro', 'capture', 'profle',
    'profile', 'camera', 'untitled', 'scene', 'scenes', 'virtual', 'recording',
    'streaming', 'dock', 'docks', 'cpu', 'gpu', 'eua', 'obs', 'ministerio',
    'help', 'aux', 'collection', 'collections', 'tools', 'iools', 'view',
    'device', 'wall', 'executiva', 'executivo', 'ceo', 'ceos', 'hipotese',
    'hipoteses', 'confissio', 'bomba', 'rebeliao', 'le', 'mlxer', 'mixer',
    'translations', 'transltlons', 'eleedit', 'profile', 'proflle', 'self',
    'attention', 'auto', 'transformer', 'algoritmo', 'mecanismo', 'atencao',
    'audio', 'funciona', 'tecnica', 'dilema', 'sobrevive', 'need', 'fps', 'ao',
    'untited', 'peco', 'traicio', 'intermacional', 'consequencias',
    'asconsequencias', 'excel',
}
TOKEN = re.compile(r"[^\W\d_][\w'’\-]*", re.UNICODE)
SPLIT = re.compile(r"[,.;:!?()\[\]{}/|]")
AFFIXES = re.compile(r"^([^\w]*)([\w'’\-]+)([^\w]*)$", re.UNICODE)
VOWELS = 'aeiouáàâãéêíóôõúü'
PHONETIC = (('ph', 'f'), ('th', 't'), ('ck', 'k'), ('qu', 'k'), ('q', 'k'),
            ('w', 'u'), ('y', 'i'), ('z', 's'))


def user_terms(path=None):
    path = Path(path) if path else USER_GLOSSARY
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    terms = value.get('terms') if isinstance(value, dict) else None
    return [str(term).strip() for term in terms or [] if str(term).strip()]


def ocr_available():
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _key(source, duration):
    try:
        stat = Path(source).stat()
        return dict(
            path=str(Path(source).resolve()),
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            duration=round(float(duration), 3),
        )
    except OSError:
        return dict(path=str(source), duration=round(float(duration), 3))


def _mask(image, exclude_roi):
    if not exclude_roi:
        return image
    image = image.copy()
    left, top, right, bottom = [float(value) for value in exclude_roi]
    scale_x, scale_y = FRAME_WIDTH / 320, FRAME_HEIGHT / 180
    left = max(0, min(FRAME_WIDTH, int(round(left * scale_x))))
    right = max(left, min(FRAME_WIDTH, int(round(right * scale_x))))
    top = max(0, min(FRAME_HEIGHT, int(round(top * scale_y))))
    bottom = max(top, min(FRAME_HEIGHT, int(round(bottom * scale_y))))
    image[top:bottom, left:right] = 0
    return image


def _ocr_lines(source, duration, exclude_roi=None):
    from rapidocr_onnxruntime import RapidOCR

    frame_bytes = FRAME_WIDTH * FRAME_HEIGHT * 3
    command = [
        media.binary('ffmpeg'),
        '-nostdin',
        '-v',
        'error',
        '-i',
        str(source),
        '-vf',
        f'fps=1/{SAMPLE_SECONDS},scale={FRAME_WIDTH}:{FRAME_HEIGHT}',
        '-pix_fmt',
        'bgr24',
        '-f',
        'rawvideo',
        '-',
    ]
    engine = RapidOCR()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lines = []
    try:
        while len(lines) < MAX_FRAMES:
            raw = process.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            image = np.frombuffer(raw, dtype=np.uint8).reshape(
                (FRAME_HEIGHT, FRAME_WIDTH, 3)
            )
            image = _mask(image, exclude_roi)
            result, _ = engine(image)
            if result:
                lines.append([str(item[1]) for item in result])
            else:
                lines.append([])
    finally:
        if process.poll() is None:
            process.kill()
        error = process.stderr.read() if process.stderr else b''
        process.wait(timeout=30)
        if process.returncode:
            raise RuntimeError(
                (error.decode('utf-8', 'replace') or 'ffmpeg OCR falhou.')[-1000:]
            )
    return lines


def _name_token(token, initial):
    core = token.strip("'’-")
    letters = [character for character in core if character.isalpha()]
    if (
        len(letters) < 2
        or any(character.isdigit() for character in core)
        or _normalized(core) in COMMON_WORDS
    ):
        return None
    if all(character.isupper() for character in letters):
        return core if 2 <= len(core) <= 6 else None
    if not core[:1].isupper():
        return None
    if any(character.isupper() for character in core[1:]):
        return core if len(core) <= 16 else None
    return None if initial else core


def _candidates(line):
    values = []
    for chunk_index, chunk in enumerate(SPLIT.split(line)):
        raw = TOKEN.findall(chunk)
        named = [
            _name_token(token, chunk_index == 0 and index == 0)
            for index, token in enumerate(raw)
        ]
        values.extend(token for token in named if token)
        run = []
        for token in named:
            if token:
                run.append(token)
                continue
            if run:
                values.extend(_phrases(run))
                run = []
        if run:
            values.extend(_phrases(run))
    return values


def _phrases(run):
    values = []
    for size in (2, 3):
        for index in range(len(run) - size + 1):
            values.append(' '.join(run[index:index + size]))
    return values


def _counts(lines):
    counts, display = Counter(), {}
    for frame in lines:
        terms = set()
        for line in frame:
            terms.update(_candidates(line))
        for term in terms:
            key = re.sub(r'\s+', ' ', term.casefold())
            counts[key] += 1
            display.setdefault(key, Counter())[term] += 1
    return counts, display


def screen_terms(source, duration, exclude_roi=None, cache_path=None):
    key = _key(source, duration)
    if cache_path is not None:
        path = Path(cache_path)
        if path.is_file():
            try:
                value = storage.read(path)
            except (ValueError, OSError):
                value = {}
            if value.get('version') == VERSION and value.get('key') == key:
                return value
    if not ocr_available():
        return dict(state='unavailable', reason='rapidocr_missing', terms=[])
    try:
        lines = _ocr_lines(source, duration, exclude_roi)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return dict(state='failed', reason=str(error)[:300], terms=[])
    counts, display = _counts(lines)
    ranked = [
        (display[key].most_common(1)[0][0], frames)
        for key, frames in counts.most_common()
    ]
    floor = max(MIN_FRAMES, round((ranked[0][1] if ranked else 0) * MIN_SHARE))
    terms = [term for term, frames in ranked if frames >= floor][:MAX_TERMS]
    value = dict(
        state='ok',
        version=VERSION,
        key=key,
        frames=len(lines),
        terms=terms,
        counts={term: counts[re.sub(r'\s+', ' ', term.casefold())] for term in terms},
    )
    if cache_path is not None:
        try:
            path = Path(cache_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            storage.write(path, value)
        except OSError:
            pass
    return value


def hotwords(terms):
    chosen, seen, chars, tokens = [], set(), 0, 0
    for term in terms:
        term = re.sub(r'\s+', ' ', str(term)).strip()
        key = term.casefold()
        if not term or key in seen:
            continue
        count = len(term.split())
        extra = len(term) + (2 if chosen else 0)
        if chars + extra > MAX_HOTWORD_CHARS or tokens + count > MAX_HOTWORD_TOKENS:
            continue
        chosen.append(term)
        seen.add(key)
        chars += extra
        tokens += count
    return ', '.join(chosen)


def _normalized(text):
    value = unicodedata.normalize('NFKD', text.casefold())
    return ''.join(character for character in value if not unicodedata.combining(character))


def _phonetic(text):
    value = re.sub(r'[^a-z]', '', _normalized(text))
    for old, new in PHONETIC:
        value = value.replace(old, new)
    value = re.sub(r'c(?=[ei])', 's', value)
    value = ''.join('a' if character in VOWELS else character for character in value)
    return re.sub(r'(.)\1+', r'\1', value)


def _terms(terms):
    result, seen = [], set()
    for term in terms:
        for token in str(term).split():
            key = token.casefold()
            if len(token) >= 2 and key not in seen:
                result.append(token)
                seen.add(key)
    return result


def _match(core, vocabulary):
    normalized = _normalized(core)
    best = None
    for term in vocabulary:
        candidate = _normalized(term)
        ratio = SequenceMatcher(None, normalized, candidate).ratio()
        phonetic = (
            not _all_caps(term)
            and len(normalized) >= 3
            and len(candidate) >= 3
            and _phonetic(core) == _phonetic(term)
        )
        if ratio >= .8 or phonetic:
            score = (1.0 if phonetic else ratio, ratio)
            if best is None or score > best[0]:
                best = (score, term)
    return best[1] if best else None


def _all_caps(text):
    letters = [character for character in text if character.isalpha()]
    return len(letters) >= 2 and all(character.isupper() for character in letters)


def _allowed(word, core, probability):
    if _normalized(core) in COMMON_WORDS:
        return False
    return probability < .6 or core[:1].isupper()


def _rebuild(segment, before):
    text = segment.get('text', '')
    cursor = 0
    for word in segment.get('words', []):
        original = before.get(word['id'], word['text'])
        index = text.find(original, cursor)
        if index < 0:
            return ' '.join(item['text'] for item in segment.get('words', [])).strip()
        if word['id'] in before:
            text = text[:index] + word['text'] + text[index + len(original):]
        cursor = index + len(word['text'])
    return text


def correct(transcript, terms):
    vocabulary = _terms(terms)
    updated = copy.deepcopy(transcript)
    changes = []
    if not vocabulary:
        return updated, changes
    for segment in updated.get('segments', []):
        before = {}
        for word in segment.get('words', []):
            text = str(word.get('text', ''))
            match = AFFIXES.match(text)
            if not match:
                continue
            leading, core, trailing = match.groups()
            term = _match(core, vocabulary)
            if term is None or _normalized(core) == _normalized(term):
                if term is not None and term != core and _allowed(
                    word, core, float(word.get('probability', 1))
                ):
                    replacement = leading + term + trailing
                    if replacement != text:
                        before[word['id']] = text
                        word.setdefault('raw_text', text)
                        word['text'] = replacement
                        changes.append(dict(
                            word_id=word['id'],
                            before=text,
                            after=replacement,
                            term=term,
                            start=word['start'],
                            end=word['end'],
                        ))
                continue
            if not _allowed(word, core, float(word.get('probability', 1))):
                continue
            replacement = leading + term + trailing
            if replacement == text:
                continue
            before[word['id']] = text
            word.setdefault('raw_text', text)
            word['text'] = replacement
            changes.append(dict(
                word_id=word['id'],
                before=text,
                after=replacement,
                term=term,
                start=word['start'],
                end=word['end'],
            ))
        if before:
            segment['text'] = _rebuild(segment, before)
    return updated, changes


def skipped(transcript, terms):
    vocabulary = _terms(terms)
    result = []
    for segment in transcript.get('segments', []):
        for word in segment.get('words', []):
            match = AFFIXES.match(str(word.get('text', '')))
            if not match:
                continue
            _leading, core, _trailing = match.groups()
            term = _match(core, vocabulary)
            if term is None or _normalized(core) == _normalized(term):
                continue
            if not _allowed(word, core, float(word.get('probability', 1))):
                result.append(dict(
                    word_id=word['id'],
                    text=word['text'],
                    term=term,
                    probability=word.get('probability'),
                    start=word['start'],
                    end=word['end'],
                ))
    return result
