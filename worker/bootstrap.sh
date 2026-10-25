#!/bin/bash
# Install the Thalweg worker on the COOL Graviton AMI. Run as root (SSM Run Command).
# cv2 comes from COOL (/opt/cool); the app venv must never install opencv-python on top of it.
set -euo pipefail
source /etc/thalweg.env
BUCKET="$THALWEG_ARTIFACTS_BUCKET"
APP=/opt/thalweg/app
mkdir -p /opt/thalweg/models "$APP"

if ! command -v aws >/dev/null; then
  apt-get update -qq && apt-get install -y -qq unzip p7zip-full >/dev/null
  curl -sSL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o /tmp/awscli.zip
  unzip -q -o /tmp/awscli.zip -d /tmp && /tmp/aws/install --update >/dev/null
fi
command -v 7z >/dev/null || (apt-get update -qq && apt-get install -y -qq p7zip-full >/dev/null)

aws s3 cp "s3://$BUCKET/deploy/thalweg.tgz" /tmp/thalweg.tgz --quiet
rm -rf "$APP" && mkdir -p "$APP" && tar -xzf /tmp/thalweg.tgz -C "$APP"
aws s3 cp "s3://$BUCKET/data/models/" /opt/thalweg/models/ --recursive --quiet

COOL_PY=/opt/cool/venvs/python_3.12/bin/python
if [ ! -x /opt/thalweg/venv/bin/python ]; then
  "$COOL_PY" -m venv /opt/thalweg/venv
fi
cat > /opt/thalweg/cool.env <<EOF
PYTHONPATH=/opt/cool/python_3.12/site-packages
LD_LIBRARY_PATH=/opt/cool/cpp_sdk/lib:/opt/ffmpeg/lib
THALWEG_MODELS=/opt/thalweg/models
THALWEG_REPO=$APP
THALWEG_DATA=/opt/thalweg/data
EOF
# Dependencies without OpenCV; numpy comes from COOL's site-packages.
/opt/thalweg/venv/bin/pip install -q --upgrade pip
/opt/thalweg/venv/bin/pip install -q "scipy>=1.14" "pydantic>=2.9" "pyyaml>=6.0" "httpx>=0.27" "boto3>=1.35" "mcp>=1.9"
/opt/thalweg/venv/bin/pip install -q --no-deps -e "$APP"

# A second interpreter with stock pip OpenCV for the A/B benchmark (never used by the service).
if [ ! -x /opt/thalweg/venv-pip/bin/python ]; then
  "$COOL_PY" -m venv /opt/thalweg/venv-pip
  /opt/thalweg/venv-pip/bin/pip install -q --upgrade pip
  /opt/thalweg/venv-pip/bin/pip install -q "opencv-python-headless==5.0.0.93" "numpy>=2.1,<3" "scipy>=1.14" "pydantic>=2.9" "pyyaml>=6.0" "httpx>=0.27" "boto3>=1.35"
  /opt/thalweg/venv-pip/bin/pip install -q --no-deps -e "$APP"
fi

# Two worker processes: a judge's run is not queued behind a scheduled replay.
systemctl disable --now thalweg-worker 2>/dev/null || true
rm -f /etc/systemd/system/thalweg-worker.service
cat > /etc/systemd/system/thalweg-worker@.service <<EOF
[Unit]
Description=Thalweg gauging worker %i
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/thalweg.env
EnvironmentFile=/opt/thalweg/cool.env
ExecStart=/opt/thalweg/venv/bin/python $APP/worker/thalweg_worker.py
Restart=always
RestartSec=5
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
for i in 1 2; do systemctl enable thalweg-worker@$i >/dev/null 2>&1; systemctl restart thalweg-worker@$i; done
sleep 3
systemctl is-active thalweg-worker@1 thalweg-worker@2
set -a; source /etc/thalweg.env; source /opt/thalweg/cool.env; set +a
/opt/thalweg/venv/bin/python -c "import cv2, thalweg; print('worker cv2', cv2.__version__, cv2.__file__)"
