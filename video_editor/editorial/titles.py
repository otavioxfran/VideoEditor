"""Ideas require human choice; a score never verifies a title's factual claims."""


def validate(value):
    if not isinstance(value, dict) or set(value) != {'titles'}:
        raise ValueError('Resposta de títulos exige apenas a lista titles.')
    titles = value['titles']
    if not isinstance(titles, list) or not titles or len(titles) > 30:
        raise ValueError('Lista de títulos inválida.')
    if not all(isinstance(t, str) and 0 < len(t.strip()) <= 250 for t in titles):
        raise ValueError('Título inválido.')
    return titles
