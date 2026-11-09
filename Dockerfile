FROM python:3.12-slim

COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt

# Commit the image was built from; run.sh passes it as a build arg.
# Declared after pip install so a new commit does not invalidate that cache.
ARG PP_BUILD_COMMIT=unknown
ENV PP_BUILD_COMMIT=${PP_BUILD_COMMIT}

COPY ppbot /ppbot

# Mini-app static files are served by the bot itself (ppbot/webapp_static).
EXPOSE 8443

WORKDIR /
CMD PYTHONPATH=. python ppbot/bot.py
