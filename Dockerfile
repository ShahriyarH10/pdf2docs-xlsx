# pdf2xlsx web app with Tesseract included: nothing to install but Docker.
#   docker build -t pdf2xlsx .
#   docker run --rm -p 127.0.0.1:8000:8000 -v "$PWD/books:/books" pdf2xlsx
# then open http://localhost:8000. Uploaded PDFs and their results are kept in ./books on your computer.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

VOLUME /books
EXPOSE 8000
# 0.0.0.0 so the published port reaches the app; it still only answers requests addressed to localhost
CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8000", "--no-browser", "--library", "/books"]
