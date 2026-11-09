FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PORT=7860

# Install only the web runtime. Local Torch/Diffusers inference remains optional;
# Hugging Face Inference API is used when its key and model are configured.
COPY requirements-min.txt ./
RUN pip install --no-cache-dir -r requirements-min.txt

# The Flask application, templates, icons, and static assets are all required.
COPY . .

EXPOSE 7860

CMD ["gunicorn", "--bind", "0.0.0.0:7860", "--workers", "1", "--worker-class", "sync", "--timeout", "60", "--keep-alive", "5", "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
