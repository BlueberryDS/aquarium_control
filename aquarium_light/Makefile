# Makefile for Aquarium SunCurve Tuya daemon
# Everything runs directly from /root/aquarium_light (no code copied elsewhere)

PROJECT_DIR := /root/aquarium_light
# Edit this if your venv python lives somewhere else:
VENV_PY     := $(PROJECT_DIR)/.venv/bin/python

SERVICE_NAME := aquarium_light_daemon.service
UNITDIR      ?= /etc/systemd/system
UNITFILE     := $(UNITDIR)/$(SERVICE_NAME)

.PHONY: all install enable start stop status uninstall

all:
	@echo "Nothing to build. Use 'make install' to install the systemd unit."

install:
	@echo "Creating systemd unit at $(UNITFILE)..."
	mkdir -p "$(UNITDIR)"
	@echo "[Unit]"                                     >  "$(UNITFILE)"
	@echo "Description=Aquarium Tuya SunCurve Light Daemon" >> "$(UNITFILE)"
	@echo "After=network-online.target"               >> "$(UNITFILE)"
	@echo "Wants=network-online.target"               >> "$(UNITFILE)"
	@echo                                             >> "$(UNITFILE)"
	@echo "[Service]"                                 >> "$(UNITFILE)"
	@echo "Type=simple"                               >> "$(UNITFILE)"
	@echo "User=root"                                 >> "$(UNITFILE)"
	@echo "WorkingDirectory=$(PROJECT_DIR)"           >> "$(UNITFILE)"
	@echo "ExecStart=$(VENV_PY) $(PROJECT_DIR)/aquarium_light_daemon.py" >> "$(UNITFILE)"
	@echo "Restart=on-failure"                        >> "$(UNITFILE)"
	@echo "RestartSec=5"                              >> "$(UNITFILE)"
	@echo "#Environment=PYTHONUNBUFFERED=1"           >> "$(UNITFILE)"
	@echo                                             >> "$(UNITFILE)"
	@echo "[Install]"                                 >> "$(UNITFILE)"
	@echo "WantedBy=multi-user.target"                >> "$(UNITFILE)"

	@echo "Reloading systemd daemon..."
	systemctl daemon-reload
	@echo
	@echo "Systemd unit installed at $(UNITFILE)."
	@echo "You can now run:"
	@echo "  make enable"
	@echo "  make start"

enable:
	@echo "Enabling $(SERVICE_NAME)..."
	systemctl enable "$(SERVICE_NAME)"

start:
	@echo "Starting $(SERVICE_NAME)..."
	systemctl start "$(SERVICE_NAME)"

stop:
	@echo "Stopping $(SERVICE_NAME)..."
	systemctl stop "$(SERVICE_NAME)" || true

status:
	systemctl status "$(SERVICE_NAME)"

uninstall:
	@echo "Stopping and disabling $(SERVICE_NAME) (if present)..."
	systemctl stop "$(SERVICE_NAME)" 2>/dev/null || true
	systemctl disable "$(SERVICE_NAME)" 2>/dev/null || true

	@echo "Removing unit file $(UNITFILE)..."
	rm -f "$(UNITFILE)"

	@echo "Reloading systemd daemon..."
	systemctl daemon-reload
	@echo "Uninstall complete."
