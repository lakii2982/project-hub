FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY --chown=app:app . .
RUN mkdir -p /app/runtime /app/staticfiles && chown -R app:app /app/runtime /app/staticfiles
USER app
RUN python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["sh", "scripts/entrypoint.sh"]
