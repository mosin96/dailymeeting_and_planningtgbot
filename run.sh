#!/bin/bash -ex

NAME=ppbot
DB_LOCATION=/db/
DB_NAME=tg_pp_bot.db
MINIAPP_PORT=8443
CERT_DIR=${CERT_DIR:-$HOME/.ppbot/certs}
CERT_FILE=${CERT_FILE:-fullchain.pem}
KEY_FILE=${KEY_FILE:-privkey.pem}

docker build -t ${NAME} .
docker rm -f ${NAME} || true
if [ -f "${CERT_DIR}/${CERT_FILE}" ] && [ -f "${CERT_DIR}/${KEY_FILE}" ]; then
  CERT_ARGS="-e PP_WEBAPP_CERT=/certs/${CERT_FILE} -e PP_WEBAPP_KEY=/certs/${KEY_FILE}"
  CERT_MOUNTS="-v ${CERT_DIR}:/certs:ro"
else
  CERT_ARGS=
  CERT_MOUNTS=
  echo "mini-app certificates not found in ${CERT_DIR}, running the mini-app over plain http (for local debugging only)"
fi
if [ -n "${PP_WEBAPP_PUBLIC_URL}" ]; then
  URL_ARGS="-e PP_WEBAPP_PUBLIC_URL=${PP_WEBAPP_PUBLIC_URL}"
else
  URL_ARGS=
fi

docker run --name ${NAME} -d --restart=unless-stopped \
  -e PP_BOT_TOKEN=${PP_BOT_TOKEN} \
  -e PP_BOT_DB_PATH=${DB_LOCATION}/${DB_NAME} \
  -v ~/.ppbot/:${DB_LOCATION} \
  ${CERT_MOUNTS} ${CERT_ARGS} ${URL_ARGS} \
  -p ${MINIAPP_PORT}:${MINIAPP_PORT} \
  ${NAME}
docker logs -f ${NAME}
