import math

MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 50


def _safe_int(value, default, minimum=1, maximum=None):
    try:
        number = int(value)
    except Exception:
        number = default
    if number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum
    return number


def get_page_args(request_args, prefix='', default_per_page=DEFAULT_PER_PAGE, max_per_page=MAX_PER_PAGE):
    page = _safe_int(request_args.get(f'{prefix}page'), 1, 1, None)
    per_page = _safe_int(request_args.get(f'{prefix}per_page'), default_per_page, 1, max_per_page)
    offset = (page - 1) * per_page
    return page, per_page, offset


def like_filter(columns, query, params):
    q = (query or '').strip()
    if not q:
        return '', params
    like = f"%{q}%"
    parts = []
    for col in columns:
        parts.append(f"COALESCE({col}, '') LIKE ?")
        params.append(like)
    return ' AND (' + ' OR '.join(parts) + ')', params


def pagination_meta(total, page, per_page):
    total = int(total or 0)
    page = int(page or 1)
    per_page = int(per_page or DEFAULT_PER_PAGE)
    pages = max(1, math.ceil(total / per_page)) if per_page else 1
    return {
        'total': total,
        'page': min(page, pages),
        'per_page': per_page,
        'pages': pages,
        'has_prev': page > 1,
        'has_next': page < pages,
        'prev_page': max(1, page - 1),
        'next_page': min(pages, page + 1),
        'start': 0 if total == 0 else ((page - 1) * per_page) + 1,
        'end': min(total, page * per_page)
    }
