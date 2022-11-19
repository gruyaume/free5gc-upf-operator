#!/usr/bin/env python3
# Copyright 2022 Guillaume Belanger
# See LICENSE file for licensing details.

"""Charmed operator for the free5GC UPF service."""

import json
import logging

from jinja2 import Environment, FileSystemLoader
from lightkube import Client
from lightkube.core.exceptions import ApiError
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.apps_v1 import StatefulSet
from lightkube.types import PatchType
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, WaitingStatus
from ops.pebble import ExecError, Layer

from network_attachment_definition import NetworkAttachmentDefinition

logger = logging.getLogger(__name__)

BASE_CONFIG_PATH = "/free5gc/config"
CONFIG_FILE_NAME = "upfcfg.yaml"
N3_NETWORK_ATTACHMENT_DEFINITION_NAME = "n3network-free5gc-v1-free5gc-upf"
N4_NETWORK_ATTACHMENT_DEFINITION_NAME = "n4network-free5gc-v1-free5gc-upf"
N6_NETWORK_ATTACHMENT_DEFINITION_NAME = "n6network-free5gc-v1-free5gc-upf"


class Free5GcUPFOperatorCharm(CharmBase):
    """Main class to describe juju event handling for the free5gc upf operator."""

    def __init__(self, *args):
        super().__init__(*args)
        self._container_name = self._service_name = "free5gc-upf"
        self._container = self.unit.get_container(self._container_name)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.remove, self._on_remove)

    def _on_config_changed(self, event) -> None:
        if not self._container.can_connect():
            self.unit.status = WaitingStatus("Waiting for container to be ready")
            event.defer()
            return
        self._write_config_file()
        if not self._n3_network_attachment_definition_created:
            self._create_n3_network_attachement_definition()
        if not self._n4_network_attachment_definition_created:
            self._create_n4_network_attachement_definition()
        if not self._n6_network_attachment_definition_created:
            self._create_n6_network_attachement_definition()
        if not self._annotation_added_to_statefulset:
            self._add_statefulset_pod_network_annotation()
        if not self._networking_rules_are_created:
            self._configure_networking_rules()
        self._container.add_layer("free5gc-upf", self._pebble_layer, combine=True)
        self._container.replan()
        self.unit.status = ActiveStatus()

    def _on_remove(self, event) -> None:
        self._delete_n3_network_attachement_definition()
        self._delete_n4_network_attachement_definition()
        self._delete_n6_network_attachement_definition()

    @property
    def _networking_rules_are_created(self) -> bool:
        process = self._container.exec(command=["ip", "rule", "list"])
        try:
            exec_return = process.wait_output()
        except ExecError as e:
            logger.error("Exited with code %d. Stderr:", e.exit_code)
            for line in e.stderr.splitlines():
                logger.error("    %s", line)
            return False
        if "from 10.1.0.0/16 table n6if" in exec_return[0]:
            logger.info("Networking rule already created")
            return True
        logger.info("Networking rule not yet created")
        return False

    def _configure_networking_rules(self) -> None:
        self._container.exec(command=["iptables-legacy", "-A", "FORWARD", "-j", "ACCEPT"])
        self._container.exec(
            command=[
                "iptables-legacy",
                "-t",
                "nat",
                "-A",
                "POSTROUTING",
                "-s",
                "10.1.0.0/16",
                "-o",
                "n6",
                "-j",
                "MASQUERADE",
            ]
        )
        self._container.exec(command=["echo", "1200 n6if", ">>", "/etc/iproute2/rt_tables"])
        self._container.exec(command=["ip", "rule", "add", "from", "10.1.0.0/16", "table", "n6if"])
        self._container.exec(
            command=[
                "ip",
                "route",
                "add",
                "default",
                "via",
                self._config_n6_gateway,
                "dev",
                "n6",
                "table" "n6if",
            ]
        )

    @property
    def _n3_network_attachment_definition_created(self) -> bool:
        return self._network_attachment_definition_created(
            name=N3_NETWORK_ATTACHMENT_DEFINITION_NAME
        )

    @property
    def _n4_network_attachment_definition_created(self) -> bool:
        return self._network_attachment_definition_created(
            name=N4_NETWORK_ATTACHMENT_DEFINITION_NAME
        )

    @property
    def _n6_network_attachment_definition_created(self) -> bool:
        return self._network_attachment_definition_created(
            name=N6_NETWORK_ATTACHMENT_DEFINITION_NAME
        )

    def _network_attachment_definition_created(self, name: str) -> bool:
        client = Client()
        try:
            client.get(
                res=NetworkAttachmentDefinition,
                name=name,
                namespace=self.model.name,
            )
            logger.info(f"NetworkAttachmentDefinition {name} already created")
            return True
        except ApiError as e:
            if e.status.reason == "NotFound":
                logger.info(f"NetworkAttachmentDefinition {name} not yet created")
                return False
        logger.info(f"Error when trying to retrieve NetworkAttachmentDefinition {name}")
        return False

    @property
    def _annotation_added_to_statefulset(self) -> bool:
        client = Client()
        statefulset = client.get(res=StatefulSet, name=self.app.name, namespace=self.model.name)
        current_annotation = statefulset.spec.template.metadata.annotations
        if "k8s.v1.cni.cncf.io/networks" in current_annotation:
            logger.info("Multus annotation already added to statefulset")
            return True
        logger.info("Multus annotation not yet added to statefulset")
        return False

    def _create_n3_network_attachement_definition(self) -> None:
        n3_nad_spec = {
            "cniVersion": "0.3.1",
            "plugins": [
                {
                    "type": "macvlan",
                    "capabilities": {"ips": True},
                    "master": self._config_n3_interface,
                    "mode": "bridge",
                    "ipam": {
                        "type": "static",
                        "routes": [{"dst": "0.0.0.0/0", "gw": self._config_n3_gateway}],
                    },
                },
                {"capabilities": {"mac": True}, "type": "tuning"},
            ],
        }
        self._create_network_attachement_definition(
            name=N3_NETWORK_ATTACHMENT_DEFINITION_NAME, spec=n3_nad_spec
        )

    def _create_n4_network_attachement_definition(self) -> None:
        n4_nad_spec = {
            "cniVersion": "0.3.1",
            "plugins": [
                {
                    "type": "macvlan",
                    "capabilities": {"ips": True},
                    "master": self._config_n4_interface,
                    "mode": "bridge",
                    "ipam": {
                        "type": "static",
                        "routes": [{"dst": "0.0.0.0/0", "gw": self._config_n4_gateway}],
                    },
                },
                {"capabilities": {"mac": True}, "type": "tuning"},
            ],
        }
        self._create_network_attachement_definition(
            name=N4_NETWORK_ATTACHMENT_DEFINITION_NAME, spec=n4_nad_spec
        )

    def _create_n6_network_attachement_definition(self) -> None:
        n6_nad_spec = {
            "cniVersion": "0.3.1",
            "plugins": [
                {
                    "type": "macvlan",
                    "capabilities": {"ips": True},
                    "master": self._config_n6_interface,
                    "mode": "bridge",
                    "ipam": {
                        "type": "static",
                        "routes": [{"dst": "0.0.0.0/0", "gw": self._config_n6_gateway}],
                    },
                },
                {"capabilities": {"mac": True}, "type": "tuning"},
            ],
        }
        self._create_network_attachement_definition(
            name=N6_NETWORK_ATTACHMENT_DEFINITION_NAME, spec=n6_nad_spec
        )

    def _create_network_attachement_definition(self, name: str, spec: dict) -> None:
        client = Client()

        nad = NetworkAttachmentDefinition(
            metadata=ObjectMeta(name=name),
            spec={"config": json.dumps(spec)},
        )
        client.create(obj=nad, namespace=self.model.name)
        logger.info(f"NetworkAttachmentDefinition {name} created")

    def _delete_n3_network_attachement_definition(self) -> None:
        self._delete_network_attachement_definition(name=N3_NETWORK_ATTACHMENT_DEFINITION_NAME)

    def _delete_n4_network_attachement_definition(self) -> None:
        self._delete_network_attachement_definition(name=N4_NETWORK_ATTACHMENT_DEFINITION_NAME)

    def _delete_n6_network_attachement_definition(self) -> None:
        self._delete_network_attachement_definition(name=N6_NETWORK_ATTACHMENT_DEFINITION_NAME)

    def _delete_network_attachement_definition(self, name: str) -> None:
        client = Client()
        client.delete(
            res=NetworkAttachmentDefinition,
            name=name,
            namespace=self.model.name,
        )
        logger.info(f"NetworkAttachmentDefinition {name} deleted")

    def _add_statefulset_pod_network_annotation(self) -> None:
        multus_annotation = [
            {
                "name": N3_NETWORK_ATTACHMENT_DEFINITION_NAME,
                "interface": "n3",
                "ips": [self._config_n3_cidr],
                "gateway": [self._config_n3_gateway],
            },
            {
                "name": N6_NETWORK_ATTACHMENT_DEFINITION_NAME,
                "interface": "n6",
                "ips": [self._config_n6_cidr],
                "gateway": [self._config_n6_gateway],
            },
            {
                "name": N4_NETWORK_ATTACHMENT_DEFINITION_NAME,
                "interface": "n4",
                "ips": [self._config_n4_cidr],
                "gateway": [self._config_n4_gateway],
            },
        ]
        client = Client()
        statefulset = client.get(res=StatefulSet, name=self.app.name, namespace=self.model.name)
        current_annotation = statefulset.spec.template.metadata.annotations
        current_annotation["k8s.v1.cni.cncf.io/networks"] = json.dumps(multus_annotation)
        client.patch(
            res=StatefulSet,
            name=self.app.name,
            obj=statefulset,
            patch_type=PatchType.MERGE,
            namespace=self.model.name,
        )
        logger.info(f"Multus annotation added to {self.app.name} Statefulset")

    def _write_config_file(self) -> None:
        jinja2_environment = Environment(loader=FileSystemLoader("src/templates/"))
        template = jinja2_environment.get_template("upfcfg.yaml.j2")
        content = template.render(
            n3_ip_address=self._config_n3_cidr.split("/")[0],
            n4_ip_address=self._config_n4_cidr.split("/")[0],
        )
        self._container.push(path=f"{BASE_CONFIG_PATH}/{CONFIG_FILE_NAME}", source=content)
        logger.info(f"Pushed {CONFIG_FILE_NAME} config file")

    @property
    def _config_n3_cidr(self) -> str:
        return self.model.config["n3-cidr"]

    @property
    def _config_n4_cidr(self) -> str:
        return self.model.config["n4-cidr"]

    @property
    def _config_n6_cidr(self) -> str:
        return self.model.config["n6-cidr"]

    @property
    def _config_n3_gateway(self) -> str:
        return self.model.config["n3-gateway"]

    @property
    def _config_n4_gateway(self) -> str:
        return self.model.config["n4-gateway"]

    @property
    def _config_n6_gateway(self) -> str:
        return self.model.config["n6-gateway"]

    @property
    def _config_n3_interface(self) -> str:
        return self.model.config["n3-interface"]

    @property
    def _config_n4_interface(self) -> str:
        return self.model.config["n4-interface"]

    @property
    def _config_n6_interface(self) -> str:
        return self.model.config["n6-interface"]

    @property
    def _pebble_layer(self) -> Layer:
        """Returns pebble layer for the charm.

        Returns:
            Layer: Pebble Layer
        """
        return Layer(
            {
                "summary": "free5gc-upf layer",
                "description": "pebble config layer for free5gc-upf",
                "services": {
                    "free5gc-upf": {
                        "override": "replace",
                        "startup": "enabled",
                        "command": "upf -c /free5gc/config/upfcfg.yaml",
                    },
                },
            }
        )


if __name__ == "__main__":
    main(Free5GcUPFOperatorCharm)
