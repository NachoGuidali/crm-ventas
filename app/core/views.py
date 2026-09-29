from django.shortcuts import render


def error_403(request, exception=None):
    return render(request, 'errores/403.html', {'mensaje': str(exception or '')}, status=403)


def error_404(request, exception=None):
    return render(request, 'errores/404.html', status=404)


def error_500(request):
    return render(request, 'errores/500.html', status=500)


def media_protegida(request, ruta):
    """
    Sirve /media/ exigiendo sesión (grabaciones, adjuntos de clientes, importaciones).
    Excepción: los adjuntos salientes de WhatsApp (nombres aleatorios) quedan públicos porque
    Meta / Twilio los descargan para enviarlos.
    """
    import mimetypes
    import os
    from django.conf import settings
    from django.http import FileResponse, Http404
    from django.shortcuts import redirect

    publica = ruta.startswith('whatsapp/salientes/')
    if not publica and not request.user.is_authenticated:
        return redirect(f'{settings.LOGIN_URL}?next={request.path}')
    base = os.path.realpath(settings.MEDIA_ROOT)
    completa = os.path.realpath(os.path.join(base, ruta))
    if not completa.startswith(base + os.sep) or not os.path.isfile(completa):
        raise Http404
    tipo, _ = mimetypes.guess_type(completa)
    resp = FileResponse(open(completa, 'rb'), content_type=tipo or 'application/octet-stream')
    resp['Cache-Control'] = 'private, max-age=3600' if not publica else 'public, max-age=86400'
    return resp
