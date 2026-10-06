# Atlas в облаке. Код берётся из GitHub при сборке — чтобы обновить облако после git push:
# Space → Settings → Factory rebuild.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face запускает контейнер от пользователя с id 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
WORKDIR /home/user

ARG ATLAS_REPO=https://github.com/alimtszyu13-svg/atlas-personal-assistant.git
RUN git clone --depth 1 "$ATLAS_REPO" app
WORKDIR /home/user/app
RUN pip install --no-cache-dir --user -r cloud/requirements.txt

EXPOSE 7860
CMD ["python", "-m", "cloud.atlas_cloud"]
