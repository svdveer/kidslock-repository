--- /addons/kidslock_manager/Dockerfile
+++ /addons/kidslock_manager/Dockerfile
@@ -0,0 +1,18 @@
+ARG BUILD_FROM
+FROM $BUILD_FROM
+
+# Install requirements for add-on
+RUN \
+  apk add --no-cache \
+    python3 \
+    python3-dev \
+    py3-pip \
+    iputils
+
+# Install Python dependencies
+COPY requirements.txt /tmp/
+RUN pip3 install --no-cache-dir -r /tmp/requirements.txt
+
+# Copy data for add-on
+COPY . /app
+WORKDIR /app
+
+RUN chmod a+x /app/run.sh
+
+CMD [ "/app/run.sh" ]
