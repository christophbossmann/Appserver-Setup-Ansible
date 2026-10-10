# rclone WebDAV server + LDAP-verification tooling for --auth-proxy.
# Adds openldap-clients (ldapsearch) + jq to the official rclone image and bakes in
# the auth-proxy script. Runtime user is set by compose (user:); the script needs
# no root (just runs ldapsearch).
FROM rclone/rclone:latest
USER root
RUN apk add --no-cache openldap-clients jq
COPY auth-proxy.sh /usr/local/bin/auth-proxy.sh
RUN chmod +x /usr/local/bin/auth-proxy.sh
