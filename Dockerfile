# dp-it-normativa-detect production image (Python). proc-protocol stdin loop on main.py. Standard library only.
FROM python:3.12-slim
WORKDIR /app
COPY *.py ./
COPY src ./src
ENV PYTHONPATH=/app/src
ENTRYPOINT ["python", "main.py"]
