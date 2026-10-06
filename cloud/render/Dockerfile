# Atlas в облаке на Render (бесплатный тариф: 512 МБ памяти — лёгкий режим памяти без модели векторов).
# Render собирает прямо из репозитория; обновление — автоматически после git push.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY cloud/render/requirements.txt cloud/render/requirements.txt
RUN pip install -r cloud/render/requirements.txt
COPY . .
# Render сам задаёт PORT; облачный Atlas его читает
CMD ["python", "-m", "cloud.atlas_cloud"]
