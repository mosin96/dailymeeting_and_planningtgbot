FROM python:3.12-slim

COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt

COPY ppbot /ppbot

# Mini-app static files are served by the bot itself (ppbot/webapp_static).
EXPOSE 8443

WORKDIR /
CMD PYTHONPATH=. python ppbot/bot.py
