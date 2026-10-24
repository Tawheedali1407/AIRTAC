# AIRTAC Advance: the public try-it copy on Hugging Face Spaces (Docker SDK, free CPU).
# It builds straight from the GitHub repo, so "Factory rebuild" on the Space picks up your latest push.
# Guest mode (airtac/guest.py): no passcode, keyword-rules brain, private plugins off, data wiped every hour.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user

ARG REPO=https://github.com/Tawheedali1407/AIRTAC.git
RUN git clone --depth 1 "$REPO" app
WORKDIR /home/user/app
RUN pip install --no-cache-dir --user -r deploy/huggingface/requirements.txt

ENV AIRTAC_GUEST=1 \
    AIRTAC_LLM=mock \
    AIRTAC_NO_LAN=1 \
    AIRTAC_SKIP_DOTENV=1 \
    AIRTAC_TRUSTED_HOSTS=.hf.space,airtacadvance.netlify.app \
    AIRTAC_GUEST_RESET_MINUTES=60

EXPOSE 7860
CMD ["python", "scripts/serve.py", "--host", "0.0.0.0", "--port", "7860"]
