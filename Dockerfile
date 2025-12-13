FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Create images directory
RUN mkdir -p uploaded_images

EXPOSE 5000

CMD ["python", "main.py"]