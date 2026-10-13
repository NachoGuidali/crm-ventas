/*
 * Teléfono del CRM: terminal SIP por WebSocket (JsSIP) que vive en el marco fijo (telefonia/marco.html).
 * Las pantallas del CRM cargan en el iframe #pantalla; navegar no recarga esta página, así que la llamada sigue.
 *
 * - Salientes: las sigue originando Click2Dial (Anura llama primero a esta terminal, que es la principal) y el
 *   teléfono atiende solo las llamadas que dispara el CRM.
 * - Entrantes: suenan acá con el nombre del cliente y el botón para abrir su ficha.
 * - Una sola pestaña registra el teléfono (Web Locks); las demás muestran el aviso.
 * Desde las pantallas: window.top.CRMTelefono.{esperarSaliente, cortar, enLlamada}.
 */
(function () {
  'use strict';
  const $ = s => document.querySelector(s);
  const bar = $('#tel'), frame = $('#pantalla'), audio = $('#audioRemoto');
  const csrf = () => $('#csrf').value;
  let ua = null, sesion = null, inicio = null, reloj = null, salienteHasta = 0, timbre = null;

  /* ── Navegación: la URL y el título de la pestaña siguen a la pantalla del iframe ── */
  frame.addEventListener('load', () => {
    try {
      const l = frame.contentWindow.location;
      if (l.pathname.startsWith('/usuarios/login')) { location.replace(l.href); return; }
      history.replaceState(null, '', l.pathname + l.search + l.hash);
      document.title = frame.contentDocument.title;
    } catch (e) { /* pantalla de otro dominio: no se sincroniza */ }
  });

  /* ── Estado en la barra ── */
  function estado(clave, texto) {
    bar.dataset.estado = clave;
    $('#tel .estado .txt').textContent = texto;
  }
  function aviso(texto) { $('#tel .aviso').textContent = texto || ''; }

  function mostrarLlamada(modo, quien) {
    bar.classList.toggle('sonando', modo === 'sonando');
    bar.classList.toggle('en-llamada', modo === 'en-llamada');
    if (quien !== undefined) pintarQuien(quien);
    const enCurso = modo === 'en-llamada';
    $('#tel .atender').hidden = modo !== 'sonando';
    ['.silenciar', '.espera', '.abrir-teclado'].forEach(s => { $('#tel ' + s).hidden = !enCurso; });
    $('#tel .cortar').innerHTML = modo === 'sonando' ? '<i class="bi bi-telephone-x"></i> Rechazar'
                                                      : '<i class="bi bi-telephone-x"></i> Cortar';
    if (!modo) { $('#teclado').hidden = true; $('#tel .reloj').textContent = ''; }
  }

  function pintarQuien(q) {
    const el = $('#tel .quien');
    el.textContent = '';
    const b = document.createElement('b');
    b.textContent = q.nombre || q.numero || 'Número privado';
    el.appendChild(b);
    if (q.nombre && q.numero) el.appendChild(document.createTextNode(' · ' + q.numero));
    const ficha = $('#tel .ver-ficha');
    ficha.hidden = !q.url;
    ficha.dataset.url = q.url || '';
  }

  $('#tel .ver-ficha').addEventListener('click', e => {
    e.preventDefault();
    if (e.currentTarget.dataset.url) frame.src = e.currentTarget.dataset.url;
  });

  function arrancarReloj() {
    inicio = Date.now();
    clearInterval(reloj);
    const tick = () => {
      const s = Math.floor((Date.now() - inicio) / 1000);
      $('#tel .reloj').textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    };
    tick(); reloj = setInterval(tick, 1000);
  }

  /* ── Timbre (Web Audio; el navegador lo permite después de un clic en el CRM) ── */
  function sonarTimbre() {
    pararTimbre();
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const pulso = () => {
        [0, 0.45].forEach(t => {
          [440, 480].forEach(f => {
            const o = ctx.createOscillator(), g = ctx.createGain();
            o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
            g.gain.setValueAtTime(0.18, ctx.currentTime + t);
            o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.38);
          });
        });
      };
      pulso();
      timbre = {ctx, id: setInterval(pulso, 3000)};
    } catch (e) { timbre = null; }
  }
  function pararTimbre() {
    if (timbre) { clearInterval(timbre.id); timbre.ctx.close().catch(() => {}); timbre = null; }
  }

  /* ── Avisar a la pantalla (iframe) para que refresque el estado de la llamada ── */
  function avisarPantalla() {
    try { frame.contentWindow.CRM && frame.contentWindow.CRM.pulsoYa && frame.contentWindow.CRM.pulsoYa(); } catch (e) { /* */ }
  }

  /* ── Sesiones ── */
  function conectarAudio(s) {
    s.on('peerconnection', e => {
      e.peerconnection.addEventListener('track', ev => { audio.srcObject = ev.streams[0]; });
    });
  }

  function terminar() {
    pararTimbre();
    clearInterval(reloj);
    sesion = null;
    mostrarLlamada(null);
    audio.srcObject = null;
    avisarPantalla();
  }

  /** ¿Es la pata del agente de una llamada que disparó el CRM (Click2Dial)? Devuelve {auto, quien}.
   *  Solo se atiende sola si el CRM está discando una llamada de este agente. La marca de "auto answer" de Anura no
   *  alcanza: con el Auto answer de la cuenta activado, Anura la pone también en las entrantes. */
  async function llamadaDelCRM(request) {
    let ll = null;
    try {
      const r = await fetch('/api/telephony/estado/', {credentials: 'same-origin'});
      ll = (await r.json()).llamada;
    } catch (e) { /* sin datos */ }
    const discando = !!(ll && ll.viva && ll.direccion === 'OUT' && ['discando', 'sonando'].includes(ll.estado));
    const quien = discando ? {nombre: ll.nombre, numero: ll.numero, url: ll.url} : null;
    const auto = discando || Date.now() < salienteHasta;
    console.info('[teléfono] INVITE', {auto, discando, callInfo: request.getHeader('Call-Info'),
                                       alertInfo: request.getHeader('Alert-Info')});
    return {auto, quien};
  }

  function atender() {
    if (!sesion) return;
    pararTimbre();
    try {
      sesion.answer({mediaConstraints: {audio: true, video: false}});
    } catch (e) {
      aviso('No se pudo atender: ' + e.message);
    }
  }

  function quienAviso(s) {
    const q = $('#tel .quien').textContent.trim();
    return q || (s.remote_identity && s.remote_identity.uri ? s.remote_identity.uri.user : 'número privado');
  }

  async function nuevaSesion(e) {
    const s = e.session;
    if (e.originator !== 'remote') return;
    console.info('[teléfono] llamada entrante a la terminal:', s.remote_identity && s.remote_identity.uri.toString());
    if (sesion && (sesion.isEnded() || !(sesion.isInProgress() || sesion.isEstablished()))) {
      console.info('[teléfono] había una llamada anterior trabada: se limpia');
      terminar();
    }
    if (sesion) {
      console.info('[teléfono] entrante rechazada: ya hay una llamada en curso');
      s.terminate({status_code: 486, reason_phrase: 'Busy Here'});
      ultimoFallo = {ts: Date.now(), texto: 'Entró otra llamada mientras hablabas: se respondió ocupado.'};
      return;
    }
    sesion = s;
    if (externa) { externa = null; bar.classList.remove('externa'); aviso(''); }  // ahora sí entró a esta terminal
    conectarAudio(s);
    const numero = s.remote_identity && s.remote_identity.uri ? s.remote_identity.uri.user : '';
    const nombreSip = s.remote_identity ? s.remote_identity.display_name : '';
    s.on('accepted', () => { s.data.atendida = Date.now(); mostrarLlamada('en-llamada'); arrancarReloj(); avisarPantalla(); });
    // Al cortar: si a los 8 s Anura todavía no avisó el fin, el CRM cierra la llamada con lo que midió el teléfono
    s.on('ended', () => {
      if (!s.data.atendida) return;
      const segundos = Math.round((Date.now() - s.data.atendida) / 1000), corto = Date.now();
      setTimeout(() => {
        fetch('/api/telephony/softphone/corto/', {
          method: 'POST', credentials: 'same-origin', headers: {'X-CSRFToken': csrf(), 'Content-Type': 'application/json'},
          body: JSON.stringify({segundos, hace: Math.round((Date.now() - corto) / 1000)}),
        }).then(avisarPantalla).catch(() => {});
      }, 8000);
    });
    s.on('confirmed', () => { mostrarLlamada('en-llamada'); });
    s.on('ended', terminar);
    s.on('failed', ev => {
      console.info('[teléfono] llamada no completada:', ev.cause, ev.originator);
      const motivos = {
        'Canceled': 'colgaron o la atendió otro teléfono antes de que la atiendas',
        'Rejected': 'rechazada', 'Busy': 'ocupado', 'No Answer': 'no se atendió a tiempo',
        'Incompatible SDP': 'problema de audio con la central (códecs)',
        'User Denied Media Access': 'el navegador no dejó usar el micrófono',
        'Connection Error': 'se perdió la conexión con la central', 'Request Timeout': 'la central no respondió',
      };
      const texto = `Llamada de ${quienAviso(s)} no completada: ${motivos[ev.cause] || ev.cause}.`;
      ultimoFallo = {ts: Date.now(), texto};
      aviso(texto);
      terminar();
    });
    s.on('getusermediafailed', () => {
      aviso('El navegador no dejó usar el micrófono: habilitalo (ícono del candado en la barra de direcciones).');
      const m = $('#tel .microfono'); if (m) m.hidden = false;
    });
    s.on('hold', () => { $('#tel .espera').classList.add('active'); });
    s.on('unhold', () => { $('#tel .espera').classList.remove('active'); });

    const crm = await llamadaDelCRM(e.request);
    if (crm.auto) {
      salienteHasta = 0;
      mostrarLlamada('en-llamada', crm.quien || {nombre: 'Llamando…', numero: ''});
      atender();
      return;
    }
    // Entrante real: el teléfono también la registra en el CRM (respaldo si Anura no manda el aviso)
    const registrar = (fase, segundos) => fetch('/api/telephony/softphone/registro/', {
      method: 'POST', credentials: 'same-origin',
      headers: {'X-CSRFToken': csrf(), 'Content-Type': 'application/json'},
      body: JSON.stringify({id: s.id, fase, numero, segundos}),
    }).then(avisarPantalla).catch(() => {});
    let atendidaEn = null;
    s.on('accepted', () => { atendidaEn = Date.now(); registrar('atendida'); });
    s.on('ended', () => { if (atendidaEn) registrar('fin', Math.round((Date.now() - atendidaEn) / 1000)); });
    mostrarLlamada('sonando', {nombre: nombreSip, numero: numero});
    sonarTimbre();
    fetch('/api/telephony/quien/?numero=' + encodeURIComponent(numero), {credentials: 'same-origin'})
      .then(r => r.json()).then(q => { if (sesion === s && (q.nombre || q.url)) pintarQuien(q); }).catch(() => {});
  }

  /* ── Controles ── */
  $('#tel .atender').addEventListener('click', atender);
  $('#tel .cortar').addEventListener('click', () => cortar());
  $('#tel .silenciar').addEventListener('click', ev => {
    if (!sesion) return;
    const b = ev.currentTarget;
    if (sesion.isMuted().audio) { sesion.unmute({audio: true}); b.classList.remove('active'); b.title = 'Silenciar micrófono'; }
    else { sesion.mute({audio: true}); b.classList.add('active'); b.title = 'Activar micrófono'; }
  });
  $('#tel .espera').addEventListener('click', () => {
    if (!sesion) return;
    if (sesion.isOnHold().local) sesion.unhold(); else sesion.hold();
  });
  $('#tel .abrir-teclado').addEventListener('click', () => { $('#teclado').hidden = !$('#teclado').hidden; });
  $('#teclado').addEventListener('click', ev => {
    const t = ev.target.closest('[data-tono]');
    if (t && sesion) { try { sesion.sendDTMF(t.dataset.tono); } catch (e) { /* */ } }
  });
  $('#tel .microfono').addEventListener('click', async ev => {
    try {
      const st = await navigator.mediaDevices.getUserMedia({audio: true});
      st.getTracks().forEach(t => t.stop());
      ev.currentTarget.hidden = true;
      aviso('');
    } catch (e) {
      aviso('El micrófono está bloqueado: habilitalo desde el candado de la barra de direcciones.');
    }
  });

  function cortar() {
    if (demoLlamada) {  // modo demostración: corta la llamada simulada del CRM
      fetch('/api/telephony/hangup/' + demoLlamada.id, {method: 'PUT', credentials: 'same-origin',
                                                        headers: {'X-CSRFToken': csrf()}})
        .finally(avisarPantalla);
      return true;
    }
    if (!sesion) return false;
    try { sesion.terminate(); } catch (e) { terminar(); }
    return true;
  }

  /* ── La pantalla informa la llamada del CRM (nombre y ficha); en modo demostración la barra la muestra ── */
  let demoLlamada = null, externa = null, ultimoFallo = null;
  function reflejar(ll) {
    if (sesion) {  // teléfono real: completar quién es
      if (ll && ll.viva && ll.nombre) pintarQuien({nombre: ll.nombre, numero: ll.numero, url: ll.url});
      return;
    }
    if (!window.CRMTelefono.demo) {
      // La central avisó una llamada de este agente que no entró a esta terminal (sonó en otro dispositivo de Anura)
      if (ll && ll.viva) {
        if (!externa || externa.id !== ll.id) {
          externa = ll;
          bar.classList.add('externa');
          mostrarLlamada('externa', {nombre: ll.nombre, numero: ll.numero, url: ll.url});
          arrancarRelojDesde(Date.now() - (ll.segundos || 0) * 1000);
          const falloReciente = ultimoFallo && Date.now() - ultimoFallo.ts < 60000;
          aviso(falloReciente ? ultimoFallo.texto : (ll.direccion === 'IN'
            ? (ua && ua.isRegistered()
              ? 'Esta llamada no llegó a este teléfono (sonó en otra terminal del interno, o va a un grupo / cola sin la terminal CRM).'
              : 'El teléfono no está conectado: la llamada no puede sonar acá.')
            : 'Llamada en otro teléfono de Anura.'));
          console.info('[teléfono] la central avisó una llamada que no llegó a esta terminal', ll);
        }
      } else if (externa) {
        externa = null;
        bar.classList.remove('externa');
        clearInterval(reloj);
        mostrarLlamada(null);
      }
      return;
    }
    if (ll && ll.viva) {
      const nueva = !demoLlamada || demoLlamada.id !== ll.id;
      demoLlamada = ll;
      mostrarLlamada('en-llamada', {nombre: ll.nombre, numero: ll.numero, url: ll.url});
      ['.silenciar', '.espera', '.abrir-teclado'].forEach(x => { $('#tel ' + x).hidden = true; });
      if (nueva || ll.atendida) { inicio = Date.now() - (ll.segundos || 0) * 1000; }
      if (nueva) arrancarRelojDesde(inicio);
    } else if (demoLlamada) {
      demoLlamada = null;
      clearInterval(reloj);
      mostrarLlamada(null);
    }
  }
  function arrancarRelojDesde(desde) {
    clearInterval(reloj);
    const tick = () => {
      const s = Math.max(0, Math.floor((Date.now() - desde) / 1000));
      $('#tel .reloj').textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    };
    tick(); reloj = setInterval(tick, 1000);
  }

  /* ── API para las pantallas del CRM (iframe) ── */
  window.CRMTelefono = {
    esperarSaliente() { salienteHasta = Date.now() + 45000; },
    cortar,
    reflejar,
    enLlamada() { return !!(sesion || demoLlamada); },
    listo() { return !!(ua && ua.isRegistered()); },
  };

  window.addEventListener('beforeunload', ev => {
    if (sesion) { ev.preventDefault(); ev.returnValue = ''; }
  });
  window.addEventListener('pagehide', () => {
    try { if (ua) ua.stop(); } catch (e) { /* */ }
  });

  /* ── Micrófono: pedir permiso de entrada si todavía no se dio ── */
  async function revisarMicrofono() {
    try {
      const p = await navigator.permissions.query({name: 'microphone'});
      const pintar = () => { const m = $('#tel .microfono'); if (m) m.hidden = p.state === 'granted'; };
      pintar(); p.onchange = pintar;
    } catch (e) { /* Firefox viejo: se pide al atender */ }
  }

  /* ── Registro SIP ── */
  async function iniciar() {
    if (!window.JsSIP) { estado('error', 'No cargó el teléfono'); return; }
    let cred;
    try {
      const r = await fetch('/api/telephony/softphone/', {method: 'POST', credentials: 'same-origin',
                                                           headers: {'X-CSRFToken': csrf()}});
      cred = await r.json();
      if (!r.ok || cred.ok === false) throw new Error(cred.error || r.status);
    } catch (e) {
      estado('error', 'Teléfono sin configurar');
      aviso(e.message);
      return;
    }
    if (cred.demo) {  // sin Anura: las llamadas las simula el CRM y se cortan desde el aviso de llamada
      estado('listo', 'Teléfono en modo demostración · interno ' + cred.interno);
      aviso('Las llamadas se simulan; para hablar de verdad, desactivá el modo demostración.');
      window.CRMTelefono.demo = true;
      $('#tel .microfono').remove();  // en demo no se usa el micrófono
      return;
    }
    if (cred.falta) {
      estado('error', 'Teléfono sin configurar · interno ' + cred.interno);
      aviso(cred.falta);
      return;
    }
    ua = new JsSIP.UA({
      sockets: [new JsSIP.WebSocketInterface(cred.ws)],
      uri: cred.uri,
      authorization_user: cred.usuario,
      password: cred.password,
      registrar_server: cred.registrar,
      display_name: cred.nombre,
      user_agent: 'CRM SupReg (JsSIP)',
      register: true,
      session_timers: false,
      connection_recovery_min_interval: 2,
      connection_recovery_max_interval: 30,
    });
    cred.password = '';
    ua.on('connecting', () => estado('conectando', 'Conectando teléfono…'));
    ua.on('connected', () => estado('conectando', 'Registrando interno ' + cred.interno + '…'));
    ua.on('disconnected', () => estado('error', 'Sin conexión con la central (reintentando)'));
    ua.on('registered', () => { estado('listo', 'Teléfono listo · interno ' + cred.interno); aviso(''); });
    ua.on('unregistered', () => estado('error', 'Teléfono desconectado'));
    ua.on('registrationFailed', e => {
      estado('error', 'No se pudo registrar el interno ' + cred.interno);
      aviso(e.cause === 'Authentication Error' ? 'Usuario o contraseña SIP incorrectos.' : (e.cause || ''));
    });
    ua.on('newRTCSession', nuevaSesion);
    ua.start();
  }

  revisarMicrofono();
  // Una sola pestaña registra el teléfono del agente. Si otra lo tiene, esta espera y lo toma apenas se cierre
  // (también cubre el instante de una recarga, cuando la página anterior todavía no lo soltó).
  if (navigator.locks) {
    const espera = setTimeout(() => {
      estado('error', 'El teléfono está abierto en otra pestaña');
      aviso('Las llamadas suenan en la otra pestaña del CRM. Si la cerrás, el teléfono pasa a esta.');
    }, 2500);
    navigator.locks.request('crm-telefono', () => {
      clearTimeout(espera);
      aviso('');
      iniciar();
      return new Promise(() => {});  // se libera solo al cerrar la pestaña
    });
  } else {
    iniciar();
  }
})();
