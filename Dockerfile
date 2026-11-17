FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DB_PATH=/data/zakupki.db PORT=8080
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY webapp ./webapp
VOLUME /data
EXPOSE 8080
CMD ["python", "-m", "app.main"]
