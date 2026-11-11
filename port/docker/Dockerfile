# Build environment for the Skylight Frame 2 kernel (32-bit ARM, cross-compiled).
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential gcc-arm-linux-gnueabihf libc6-dev-armhf-cross \
      bc bison flex libssl-dev libelf-dev kmod cpio xz-utils lzop \
      device-tree-compiler ca-certificates curl gnupg python3 rsync file \
    && apt-get clean
ENV ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf-
WORKDIR /build
