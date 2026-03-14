FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar aplicación
COPY . .

# Crear directorio para base de datos
RUN mkdir -p instance

# Variables de entorno por defecto
ENV PRODUCTION=True
ENV SECRET_KEY=default-change-this-in-production

# Exponer puerto
EXPOSE 5000

# Ejecutar con gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]