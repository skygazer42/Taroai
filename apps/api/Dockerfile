FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV PIP_DEFAULT_TIMEOUT=120
ENV PIP_RETRIES=10

WORKDIR /app

RUN addgroup --system taroai && adduser --system --ingroup taroai taroai

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --retries 10 --timeout 120 -r /app/requirements.txt

COPY src /app/src
COPY migrations /app/migrations
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh && chown -R taroai:taroai /app

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "taroai.app:app", "--host", "0.0.0.0", "--port", "8000"]
