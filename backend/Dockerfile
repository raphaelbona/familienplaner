FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# URLLIB3_NO_OVERRIDE: caldav -> niquests -> urllib3-future ships a .pth hook that
# overwrites the real urllib3 package on disk unless this is set at install time.
ENV URLLIB3_NO_OVERRIDE=1
# Optional build secret `build_ca`: a CA bundle for networks that intercept TLS (e.g. antivirus
# SSL scanning). Mounted only for this step, so it never ends up in an image layer.
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi \
    && pip install --no-cache-dir --no-binary urllib3-future -r requirements.txt \
    && pip install --no-cache-dir "urllib3>=2,<3"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
