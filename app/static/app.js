const form = document.querySelector('#extract-form');
const submitButton = document.querySelector('#submit');
const statusBox = document.querySelector('#status');
const progressBar = document.querySelector('#progress');
const downloadsBox = document.querySelector('#downloads');
const reportsBox = document.querySelector('#reports');
const previewBox = document.querySelector('#preview');

const ttsForm = document.querySelector('#tts-form');
const ttsSubmitButton = document.querySelector('#tts_submit');
const ttsStatusBox = document.querySelector('#tts_status');
const ttsProgressBar = document.querySelector('#tts_progress');
const ttsDownloadsBox = document.querySelector('#tts_downloads');
const ttsPreviewBox = document.querySelector('#tts_preview');
const ttsStatsBox = document.querySelector('#tts_stats');
const ttsAudioBox = document.querySelector('#tts_audio_box');
const ttsProviderSelect = document.querySelector('#tts_provider');
const ttsVoiceSelect = document.querySelector('#tts_voice');

const checkboxIds = [
  'remove_repeated_headers_footers',
  'strip_page_numbers',
  'include_image_captions',
  'mark_captions',
  'preserve_line_breaks',
  'page_breaks',
  'sort_reading_order',
  'generate_searchable_pdf',
];

const ratioPercentIds = [
  'top_margin_pct',
  'bottom_margin_pct',
  'left_margin_pct',
  'right_margin_pct',
  'auto_header_footer_zone_pct',
  'repeated_line_min_ratio',
];

const edgeVoices = [
  ['es-ES-ElviraNeural', 'Elvira — femenina, clara'],
  ['es-ES-AlvaroNeural', 'Álvaro — masculina, natural'],
  ['es-ES-XimenaNeural', 'Ximena — femenina'],
  ['es-ES-VeraNeural', 'Vera — femenina'],
  ['es-ES-AbrilNeural', 'Abril — femenina'],
  ['es-ES-LaiaNeural', 'Laia — femenina'],
  ['es-ES-LiaNeural', 'Lia — femenina'],
  ['es-ES-TrianaNeural', 'Triana — femenina'],
  ['es-ES-IreneNeural', 'Irene — femenina'],
  ['es-ES-EstrellaNeural', 'Estrella — femenina'],
  ['es-ES-NilNeural', 'Nil — masculina'],
  ['es-ES-ArnauNeural', 'Arnau — masculina'],
  ['es-ES-DarioNeural', 'Darío — masculina'],
  ['es-ES-EliasNeural', 'Elías — masculina'],
  ['es-ES-SaulNeural', 'Saúl — masculina'],
  ['es-ES-TeoNeural', 'Teo — masculina'],
];

const piperVoices = [
  ['es_ES-sharvard-medium', 'Piper sharvard — local neural'],
  ['es_ES-davefx-medium', 'Piper davefx — local neural'],
  ['es_ES-carlfm-x_low', 'Piper carlfm — local ligero'],
];

const espeakVoices = [
  ['es-es', 'eSpeak es-es — local básico'],
];

initializeTabs();
initializeTtsVoices();

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearResult();
  const data = buildFormData();
  submitButton.disabled = true;
  setStatus('Subiendo PDF...', 'running', 0.02);
  try {
    const response = await fetch('/api/jobs', { method: 'POST', body: data });
    const payload = await response.json();
    if (!response.ok) throw new Error(formatApiError(payload));
    pollJob(payload.job_id);
  } catch (error) {
    setStatus(error.message || String(error), 'failed', 1);
    submitButton.disabled = false;
  }
});

ttsForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearTtsResult();
  const data = buildTtsFormData();
  ttsSubmitButton.disabled = true;
  setTtsStatus('Subiendo TXT...', 'running', 0.02);
  try {
    const response = await fetch('/api/tts/jobs', { method: 'POST', body: data });
    const payload = await response.json();
    if (!response.ok) throw new Error(formatApiError(payload));
    pollTtsJob(payload.job_id);
  } catch (error) {
    setTtsStatus(error.message || String(error), 'failed', 1);
    ttsSubmitButton.disabled = false;
  }
});

function initializeTabs() {
  const buttons = document.querySelectorAll('.tab-button');
  const panels = document.querySelectorAll('.tab-panel');
  for (const button of buttons) {
    button.addEventListener('click', () => {
      const tab = button.dataset.tab;
      for (const otherButton of buttons) otherButton.classList.toggle('active', otherButton === button);
      for (const panel of panels) panel.classList.toggle('active', panel.id === `tab-${tab}`);
    });
  }
}

function initializeTtsVoices() {
  ttsProviderSelect.addEventListener('change', refreshTtsVoiceOptions);
  refreshTtsVoiceOptions();
}

function refreshTtsVoiceOptions() {
  const provider = ttsProviderSelect.value;
  let voices = edgeVoices;
  if (provider === 'piper') voices = piperVoices;
  if (provider === 'espeak') voices = espeakVoices;
  if (provider === 'auto') voices = edgeVoices.concat(piperVoices);

  const current = ttsVoiceSelect.value;
  ttsVoiceSelect.innerHTML = '';
  const seen = new Set();
  for (const [value, label] of voices) {
    if (seen.has(value)) continue;
    seen.add(value);
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    ttsVoiceSelect.appendChild(option);
  }
  if ([...ttsVoiceSelect.options].some((option) => option.value === current)) {
    ttsVoiceSelect.value = current;
  } else if (provider === 'piper') {
    ttsVoiceSelect.value = 'es_ES-sharvard-medium';
  } else if (provider === 'espeak') {
    ttsVoiceSelect.value = 'es-es';
  } else {
    ttsVoiceSelect.value = 'es-ES-ElviraNeural';
  }
}

function buildFormData() {
  const data = new FormData();
  const file = document.querySelector('#file').files[0];
  data.append('file', file);
  const simpleIds = [
    'start_page', 'end_page', 'mode', 'ocr_strategy', 'language', 'ocr_dpi',
    'native_min_chars', 'native_min_words'
  ];
  for (const id of simpleIds) {
    const value = document.querySelector(`#${id}`).value;
    if (value !== '') data.append(id, value);
  }
  for (const id of checkboxIds) {
    data.append(id, document.querySelector(`#${id}`).checked ? 'true' : 'false');
  }
  for (const id of ratioPercentIds) {
    const raw = Number(document.querySelector(`#${id}`).value || 0);
    data.append(id, String(raw / 100));
  }
  return data;
}

function buildTtsFormData() {
  const data = new FormData();
  const file = document.querySelector('#tts_file').files[0];
  data.append('file', file);
  const ids = [
    'tts_provider', 'tts_voice', 'tts_audio_format', 'tts_rate', 'tts_pitch_hz',
    'tts_volume', 'tts_bitrate_kbps', 'tts_chunk_chars', 'tts_title'
  ];
  const mapping = {
    tts_provider: 'provider',
    tts_voice: 'voice',
    tts_audio_format: 'audio_format',
    tts_rate: 'rate',
    tts_pitch_hz: 'pitch_hz',
    tts_volume: 'volume',
    tts_bitrate_kbps: 'bitrate_kbps',
    tts_chunk_chars: 'chunk_chars',
    tts_title: 'title',
  };
  for (const id of ids) {
    const value = document.querySelector(`#${id}`).value;
    if (value !== '') data.append(mapping[id], value);
  }
  data.append('normalize_for_narration', document.querySelector('#tts_normalize_for_narration').checked ? 'true' : 'false');
  data.append('remove_page_markers', document.querySelector('#tts_remove_page_markers').checked ? 'true' : 'false');
  return data;
}

async function pollJob(jobId) {
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(formatApiError(job));
    renderJob(job);
    if (job.status === 'queued' || job.status === 'running') {
      window.setTimeout(() => pollJob(jobId), 900);
    } else {
      submitButton.disabled = false;
    }
  } catch (error) {
    setStatus(error.message || String(error), 'failed', 1);
    submitButton.disabled = false;
  }
}

async function pollTtsJob(jobId) {
  try {
    const response = await fetch(`/api/tts/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(formatApiError(job));
    renderTtsJob(job);
    if (job.status === 'queued' || job.status === 'running') {
      window.setTimeout(() => pollTtsJob(jobId), 900);
    } else {
      ttsSubmitButton.disabled = false;
    }
  } catch (error) {
    setTtsStatus(error.message || String(error), 'failed', 1);
    ttsSubmitButton.disabled = false;
  }
}

function renderJob(job) {
  setStatus(`${job.message} (${Math.round((job.progress || 0) * 100)}%)`, job.status, job.progress || 0);
  if (job.error) {
    previewBox.textContent = job.error;
  } else if (job.preview) {
    previewBox.textContent = job.preview;
  }
  renderDownloads(job.downloads || {});
  renderReports(job.page_reports || []);
}

function renderTtsJob(job) {
  setTtsStatus(`${job.message} (${Math.round((job.progress || 0) * 100)}%)`, job.status, job.progress || 0);
  if (job.error) {
    ttsPreviewBox.textContent = job.error;
  } else if (job.preview) {
    ttsPreviewBox.textContent = job.preview;
  }
  renderTtsDownloads(job.downloads || {}, job.stats || {});
  renderTtsStats(job.stats || {});
}

function renderDownloads(downloads) {
  downloadsBox.innerHTML = '';
  const labels = {
    txt: 'Descargar TXT',
    md: 'Descargar Markdown',
    docx: 'Descargar DOCX',
    json: 'Descargar JSON',
    searchable_pdf: 'Descargar PDF buscable',
  };
  for (const [key, url] of Object.entries(downloads)) {
    const link = document.createElement('a');
    link.href = url;
    link.textContent = labels[key] || key;
    link.download = '';
    downloadsBox.appendChild(link);
  }
}

function renderTtsDownloads(downloads, stats) {
  ttsDownloadsBox.innerHTML = '';
  ttsAudioBox.innerHTML = '';
  const labels = {
    audio: `Descargar ${String(stats.audio_format || 'audio').toUpperCase()}`,
    metadata: 'Descargar metadatos JSON',
  };
  if (downloads.audio) {
    const audio = document.createElement('audio');
    audio.controls = true;
    audio.src = downloads.audio;
    audio.className = 'audio-player';
    ttsAudioBox.appendChild(audio);
  }
  for (const [key, url] of Object.entries(downloads)) {
    const link = document.createElement('a');
    link.href = url;
    link.textContent = labels[key] || key;
    link.download = '';
    ttsDownloadsBox.appendChild(link);
  }
}

function renderTtsStats(stats) {
  if (!stats || (!stats.provider_used && !stats.chars)) {
    ttsStatsBox.innerHTML = '';
    return;
  }
  const duration = stats.duration_seconds ? formatDuration(stats.duration_seconds) : 'No disponible';
  const warnings = (stats.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join('');
  ttsStatsBox.innerHTML = `
    <table class="report-table">
      <tbody>
        <tr><th>Motor usado</th><td>${escapeHtml(stats.provider_used || '')}</td></tr>
        <tr><th>Voz usada</th><td>${escapeHtml(stats.voice_used || '')}</td></tr>
        <tr><th>Formato</th><td>${escapeHtml((stats.audio_format || '').toUpperCase())}</td></tr>
        <tr><th>Caracteres</th><td>${escapeHtml(stats.chars || 0)}</td></tr>
        <tr><th>Palabras</th><td>${escapeHtml(stats.words || 0)}</td></tr>
        <tr><th>Fragmentos</th><td>${escapeHtml(stats.chunks || 0)}</td></tr>
        <tr><th>Duración</th><td>${escapeHtml(duration)}</td></tr>
      </tbody>
    </table>
    ${warnings ? `<div class="warning-list"><strong>Avisos:</strong><ul>${warnings}</ul></div>` : ''}`;
}

function renderReports(reports) {
  if (!reports.length) {
    reportsBox.innerHTML = '';
    return;
  }
  const rows = reports.map((r) => `
    <tr>
      <td>${escapeHtml(r.page_number)}</td>
      <td>${escapeHtml(r.source)}</td>
      <td>${escapeHtml(r.chars_after_cleanup)}</td>
      <td>${escapeHtml(r.words_after_cleanup)}</td>
      <td>${escapeHtml(r.removed_lines)}</td>
      <td>${escapeHtml(r.caption_lines)}</td>
      <td>${r.ocr_mean_confidence ?? ''}</td>
      <td>${escapeHtml((r.warnings || []).join(' | '))}</td>
    </tr>`).join('');
  reportsBox.innerHTML = `
    <table class="report-table">
      <thead>
        <tr><th>Pág.</th><th>Fuente</th><th>Car.</th><th>Pal.</th><th>Quitadas</th><th>Pies</th><th>Conf. OCR</th><th>Avisos</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function setStatus(message, kind, progress) {
  statusBox.textContent = message;
  statusBox.className = `status ${kind || ''}`;
  progressBar.style.width = `${Math.max(0, Math.min(1, progress || 0)) * 100}%`;
}

function setTtsStatus(message, kind, progress) {
  ttsStatusBox.textContent = message;
  ttsStatusBox.className = `status ${kind || ''}`;
  ttsProgressBar.style.width = `${Math.max(0, Math.min(1, progress || 0)) * 100}%`;
}

function clearResult() {
  downloadsBox.innerHTML = '';
  reportsBox.innerHTML = '';
  previewBox.textContent = '';
  setStatus('Preparando...', 'running', 0);
}

function clearTtsResult() {
  ttsDownloadsBox.innerHTML = '';
  ttsAudioBox.innerHTML = '';
  ttsStatsBox.innerHTML = '';
  ttsPreviewBox.textContent = '';
  setTtsStatus('Preparando...', 'running', 0);
}

function formatApiError(payload) {
  if (payload?.detail) {
    if (Array.isArray(payload.detail)) return payload.detail.map((item) => item.msg || JSON.stringify(item)).join('\n');
    return String(payload.detail);
  }
  return 'Error inesperado';
}

function formatDuration(seconds) {
  const total = Math.round(Number(seconds) || 0);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = total % 60;
  if (hours) return `${hours} h ${minutes} min ${remaining} s`;
  if (minutes) return `${minutes} min ${remaining} s`;
  return `${remaining} s`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
