FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 hexa
COPY --chown=hexa:hexa hexa_userbot.py pyrogram_transport.py telegram_credentials.py ./
USER hexa

CMD ["python", "hexa_userbot.py", "--non-interactive"]
