import json

from django.core.paginator import Paginator
from django.http import JsonResponse


def paginar(request, queryset, por_pagina=50):
    paginator = Paginator(queryset, por_pagina)
    return paginator.get_page(request.GET.get('page'))


def json_body(request):
    """Lee JSON o form-data indistintamente (los endpoints AJAX aceptan ambos)."""
    if request.content_type and 'json' in request.content_type:
        try:
            return json.loads(request.body or b'{}')
        except (ValueError, TypeError):
            return {}
    return request.POST.dict()


def ok(**data):
    return JsonResponse({'ok': True, **data})


def error(mensaje, status=400, **data):
    return JsonResponse({'ok': False, 'error': mensaje, **data}, status=status)


def query_sin_page(request):
    """Querystring actual sin el parámetro page (para links de paginación)."""
    params = request.GET.copy()
    params.pop('page', None)
    return params.urlencode()


def sin_autocompletar(form):
    """Formularios de configuración con claves (tokens, API keys, contraseñas de servicios): que el navegador no los
    llene con el usuario y la contraseña guardados del CRM ("admin" en la URL, la clave del CRM en la API key)."""
    from django import forms
    for f in form.fields.values():
        w = f.widget
        if isinstance(w, forms.PasswordInput):
            w.attrs['autocomplete'] = 'new-password'
        elif isinstance(w, (forms.TextInput, forms.URLInput, forms.EmailInput, forms.NumberInput)):
            w.attrs.setdefault('autocomplete', 'off')
    return form
