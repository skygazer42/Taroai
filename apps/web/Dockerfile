FROM nginxinc/nginx-unprivileged:1.27-alpine

RUN sed -i '/http {/a\\    client_max_body_size 35m;' /etc/nginx/nginx.conf

COPY index.html /usr/share/nginx/html/index.html
COPY assets /usr/share/nginx/html/assets

EXPOSE 8080
