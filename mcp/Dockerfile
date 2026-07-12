FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency install
RUN pip install uv

COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

COPY . .

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "server.py"]
