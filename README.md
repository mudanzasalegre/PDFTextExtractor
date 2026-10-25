# Extractor PDF y Audio Español

Aplicación local para dos flujos de trabajo:

1. **PDF → texto**: extrae texto íntegro de rangos de páginas en PDFs en español. Detecta por página si existe texto real y, cuando no lo hay, usa OCR con Tesseract.
2. **TXT → audio**: convierte un `.txt` limpio en un archivo de audio leído con voces de español de España.

## Qué hace

### PDF → texto

- Extrae texto de un rango 1-based inclusivo, por ejemplo páginas 5 a 19.
- Detecta automáticamente páginas con texto nativo frente a páginas escaneadas.
- Usa OCR en español por defecto (`spa+eng`).
- Permite quitar cabeceras, pies repetidos y números de página.
- Permite conservar o excluir pies de imagen/tabla detectados.
- Permite definir márgenes superior, inferior, izquierdo y derecho.
- Reconstruye orden de lectura con heurística para una o dos columnas.
- Exporta TXT, Markdown, DOCX y JSON.
- Opcionalmente genera un PDF buscable con OCRmyPDF si se activa la casilla correspondiente.

### TXT → audio

- Sube un `.txt` ya limpio y lo convierte a voz.
- Restringe las voces a español de España: `es-ES`, `es_ES` o `es-es`.
- Modo automático:
  - intenta primero **Edge neural online** con voces es-ES de alta calidad;
  - si falla, intenta **Piper local** con modelos es_ES;
  - si también falla, usa **eSpeak local** como respaldo básico.
- Exporta MP3 o WAV.
- Permite ajustar velocidad, tono, volumen, bitrate y tamaño de fragmentos.
- Normaliza saltos de línea para que el TXT se lea como narración natural.
- Puede quitar marcadores de página tipo `--- Página 5 ---`.
- Muestra reproductor de audio en la propia web y descarga del archivo final.

## Arranque con Docker

```bash
docker compose up --build
```

Después abre:

```text
http://localhost:8000
```

La primera compilación de Docker descarga dependencias Python y modelos locales Piper para español de España. Necesita internet durante el `docker compose up --build`. Una vez construida la imagen, el fallback Piper puede funcionar sin internet.

## Arranque sin Docker

Necesitas Python 3.12+, Tesseract, OCRmyPDF, ffmpeg y eSpeak NG.

En Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y \
  tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng \
  ocrmypdf ghostscript qpdf unpaper \
  ffmpeg espeak-ng libgomp1

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

En macOS:

```bash
brew install tesseract ocrmypdf ffmpeg espeak-ng
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Para usar Piper sin Docker, descarga los modelos `.onnx` y `.onnx.json` en la carpeta indicada por `PIPER_MODELS_DIR`. Por defecto se espera:

```text
/app/models/piper/es_ES-sharvard-medium.onnx
/app/models/piper/es_ES-sharvard-medium.onnx.json
/app/models/piper/es_ES-davefx-medium.onnx
/app/models/piper/es_ES-davefx-medium.onnx.json
/app/models/piper/es_ES-carlfm-x_low.onnx
/app/models/piper/es_ES-carlfm-x_low.onnx.json
```

En ejecución local sin Docker puedes definir otra ruta:

```bash
export PIPER_MODELS_DIR="$PWD/models/piper"
```

## Uso recomendado

### Para libros, artículos y documentos académicos

- Pestaña: `PDF → texto`
- Modo: `Académico / documental`
- OCR: `Automático`
- Idioma: `spa+eng`
- Mantener `Incluir pies de imagen/tabla` activado
- Mantener `Quitar cabeceras/pies repetidos` activado
- Dejar márgenes al 0% salvo que sepas que el margen contiene ruido

### Para obtener todo exactamente, incluyendo cabeceras, pies y numeración

- Pestaña: `PDF → texto`
- Modo: `Fiel al PDF`

### Para crear audio a partir del TXT limpio

- Pestaña: `TXT → audio`
- Motor: `Automático`
- Voz: `Elvira` o `Álvaro`
- Formato: `MP3`
- Velocidad: `-5%` suele ser cómoda para escucha larga
- Mantener activado `Normalizar saltos de línea para lectura natural`

## API

### PDF → texto

Crear un trabajo:

```bash
curl -F "file=@documento.pdf" \
  -F "start_page=5" \
  -F "end_page=19" \
  -F "mode=academic" \
  -F "ocr_strategy=auto" \
  -F "language=spa+eng" \
  http://localhost:8000/api/jobs
```

Consultar estado:

```bash
curl http://localhost:8000/api/jobs/<job_id>
```

Descargar resultados:

```bash
curl -L -o resultado.txt  http://localhost:8000/api/jobs/<job_id>/download/txt
curl -L -o resultado.md   http://localhost:8000/api/jobs/<job_id>/download/md
curl -L -o resultado.docx http://localhost:8000/api/jobs/<job_id>/download/docx
curl -L -o resultado.json http://localhost:8000/api/jobs/<job_id>/download/json
```

### TXT → audio

Crear un trabajo:

```bash
curl -F "file=@texto_limpio.txt" \
  -F "provider=auto" \
  -F "voice=es-ES-ElviraNeural" \
  -F "audio_format=mp3" \
  -F "rate=-5" \
  http://localhost:8000/api/tts/jobs
```

Consultar estado:

```bash
curl http://localhost:8000/api/tts/jobs/<job_id>
```

Descargar audio y metadatos:

```bash
curl -L -o audio.mp3      http://localhost:8000/api/tts/jobs/<job_id>/download/audio
curl -L -o audio_meta.json http://localhost:8000/api/tts/jobs/<job_id>/download/metadata
```

## Ajustes importantes

Variables de entorno:

```text
STORAGE_DIR=storage
MAX_UPLOAD_MB=300
MAX_TTS_UPLOAD_MB=50
MAX_TTS_CHARS=750000
MAX_WORKERS=2
MAX_TTS_WORKERS=1
PIPER_MODELS_DIR=/app/models/piper
PIPER_DEFAULT_VOICE=es_ES-sharvard-medium
CORS_ALLOW_ORIGINS=*
```

`MAX_WORKERS` controla cuántos PDFs se procesan en paralelo. Para OCR intensivo, 1–2 suele ser más estable en portátiles.

`MAX_TTS_WORKERS` controla cuántos audios se generan en paralelo. Para evitar saturar CPU o servicios externos, 1 suele ser lo más prudente.

## Limitaciones honestas

La extracción de PDFs no es perfecta por naturaleza. Esta app cubre casos reales bastante bien, pero documentos con maquetación extrema pueden requerir ajustes de márgenes o modo. Los casos más difíciles son:

- escaneos borrosos, torcidos o de baja resolución;
- tablas complejas;
- notas laterales;
- documentos con tres o más columnas;
- textos con capa OCR previa de mala calidad;
- imágenes con texto incrustado dentro de la propia imagen.

Para TXT → audio:

- La mejor calidad del modo automático suele ser `edge-tts`, que necesita internet en tiempo de ejecución.
- Piper es local y privado, pero su naturalidad depende del modelo instalado.
- eSpeak es muy robusto y local, pero suena mucho menos natural.
- No se clonan voces ni se imita a personas concretas; solo se usan voces TTS genéricas de español de España.
- Textos muy largos se dividen en fragmentos y luego se unen con ffmpeg.

## Tests

```bash
pytest -q
```
