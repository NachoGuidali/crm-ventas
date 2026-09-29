import json

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from core.permisos import PermisoRequeridoMixin
from core.utils import paginar, query_sin_page

from .models import AccionEtapa, EjecucionAccion


class AccionForm(forms.ModelForm):
    demora_valor = forms.IntegerField(min_value=0, initial=0, label='Demora')
    demora_unidad = forms.ChoiceField(choices=[('min', 'minutos'), ('h', 'horas'), ('d', 'días')], initial='min')

    class Meta:
        model = AccionEtapa
        fields = ['nombre', 'etapa', 'tipo', 'activa', 'solo_si_sigue_en_etapa', 'solo_en_horario', 'plantilla', 'texto',
                  'linea', 'email_asunto', 'tarea_titulo', 'tarea_vence_horas']
        widgets = {'texto': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, embudo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.embudo = embudo
        if embudo is not None:
            self.fields['etapa'].queryset = embudo.etapas.order_by('orden')
        m = self.instance.demora_minutos or 0
        if m and m % 1440 == 0:
            self.fields['demora_valor'].initial, self.fields['demora_unidad'].initial = m // 1440, 'd'
        elif m and m % 60 == 0:
            self.fields['demora_valor'].initial, self.fields['demora_unidad'].initial = m // 60, 'h'
        else:
            self.fields['demora_valor'].initial = m
        for f in self.fields.values():
            w = f.widget
            w.attrs['class'] = ('form-check-input' if isinstance(w, forms.CheckboxInput)
                                else 'form-select' if isinstance(w, forms.Select) else 'form-control')

    def clean(self):
        d = super().clean()
        tipo = d.get('tipo')
        if tipo == AccionEtapa.TIPO_WHATSAPP and not (d.get('plantilla') or d.get('texto')):
            self.add_error('plantilla', 'Elegí una plantilla o escribí un texto.')
        if tipo == AccionEtapa.TIPO_EMAIL and not d.get('texto'):
            self.add_error('texto', 'Escribí el cuerpo del email.')
        return d

    def save(self, commit=True):
        factor = {'min': 1, 'h': 60, 'd': 1440}[self.cleaned_data.get('demora_unidad') or 'min']
        self.instance.demora_minutos = (self.cleaned_data.get('demora_valor') or 0) * factor
        if self.embudo is not None:
            self.instance.embudo = self.embudo
        return super().save(commit)


class AccionListView(PermisoRequeridoMixin, View):
    permiso = 'automatizaciones'

    def get(self, request):
        from apps.crm.views import embudo_actual
        embudo, embudos = embudo_actual(request)
        etapas = []
        if embudo:
            acciones = list(AccionEtapa.objects.filter(embudo=embudo).select_related('plantilla', 'linea'))
            for etapa in embudo.etapas.order_by('orden'):
                etapas.append({'etapa': etapa, 'acciones': [a for a in acciones if a.etapa_id == etapa.pk]})
        return render(request, 'automatizaciones/lista.html', {'embudo': embudo, 'embudos': embudos, 'etapas': etapas})


class AccionEditarView(PermisoRequeridoMixin, View):
    permiso = 'automatizaciones'

    def get(self, request, pk=None):
        from apps.crm.views import embudo_actual
        accion = get_object_or_404(AccionEtapa, pk=pk) if pk else None
        embudo = accion.embudo if accion else embudo_actual(request)[0]
        inicial = {'etapa': request.GET.get('etapa')} if not accion else None
        return self._render(request, accion, embudo, AccionForm(instance=accion, embudo=embudo, initial=inicial))

    def post(self, request, pk=None):
        from apps.crm.views import embudo_actual
        accion = get_object_or_404(AccionEtapa, pk=pk) if pk else None
        embudo = accion.embudo if accion else embudo_actual(request)[0]
        if request.POST.get('eliminar') and accion:
            accion.delete()
            messages.success(request, 'Automatización eliminada.')
            return redirect('automatizaciones:lista')
        if request.POST.get('toggle') and accion:
            accion.activa = not accion.activa
            accion.save(update_fields=['activa'])
            messages.success(request, f'"{accion}" {"activada" if accion.activa else "desactivada"}.')
            return redirect('automatizaciones:lista')
        form = AccionForm(request.POST, instance=accion, embudo=embudo)
        if not form.is_valid():
            return self._render(request, accion, embudo, form)
        form.save()
        messages.success(request, 'Automatización guardada.')
        return redirect('automatizaciones:lista')

    def _render(self, request, accion, embudo, form):
        from apps.whatsapp.models import Plantilla
        return render(request, 'automatizaciones/form.html', {
            'accion': accion, 'embudo': embudo, 'form': form,
            'plantillas_texto': json.dumps({str(p.pk): p.cuerpo for p in Plantilla.objects.filter(activa=True)}).replace('</', '<\\/'),
        })


class EjecucionesView(PermisoRequeridoMixin, View):
    permiso = ('automatizaciones', 'supervision')

    def get(self, request):
        qs = EjecucionAccion.objects.select_related('accion', 'oportunidad__contacto')
        if request.GET.get('estado'):
            qs = qs.filter(estado=request.GET['estado'])
        return render(request, 'automatizaciones/ejecuciones.html', {
            'page': paginar(request, qs, 50), 'estados': EjecucionAccion.ESTADO_CHOICES, 'filtros': request.GET,
            'query': query_sin_page(request),
        })
