FROM python:3.12-slim
WORKDIR /app
COPY main.py dial.py ./
# stdlib only — no requirements. State volume mounted at /data.
ENV STATE_DIR=/data POLL_SECS=60
CMD ["sh", "-c", "python main.py"]
