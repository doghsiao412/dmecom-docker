FROM python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Taipei
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libcap2-bin \
    && setcap cap_net_raw=ep /usr/local/bin/python3.11 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --chmod=0444 dmecom.py discovery.py backup.py voice.py web_security.py device_clock.py answer.py settings_ui.py archive_migration.py connection_status.py recording_filters.py pcm_cleanup.py announcement.py announcement_ui.py ui_layout.py device_voices.py device_voices_ui.py voice_names.py media.py recording_ui.py live_monitor.py live_ui.py server.py /app/
CMD ["python3", "server.py"]
