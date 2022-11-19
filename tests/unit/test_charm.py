# Copyright 2022 Guillaume Belanger
# See LICENSE file for licensing details.

import json
import unittest
from unittest.mock import call, patch

from lightkube.models.apps_v1 import StatefulSet, StatefulSetSpec
from lightkube.models.core_v1 import PodTemplateSpec
from lightkube.models.meta_v1 import LabelSelector, ObjectMeta
from ops import testing

from charm import Free5GcUPFOperatorCharm
from network_attachment_definition import NetworkAttachmentDefinition

testing.SIMULATE_CAN_CONNECT = True


class TestCharm(unittest.TestCase):
    def setUp(self):
        self.model_name = "whatever"
        self.harness = testing.Harness(Free5GcUPFOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.set_model_name(name=self.model_name)
        self.harness.begin()

    @patch("lightkube.Client.patch")
    @patch("lightkube.Client.create")
    @patch("lightkube.Client.get")
    @patch("ops.model.Container.exec")
    @patch("ops.model.Container.push")
    @patch("lightkube.core.client.GenericSyncClient")
    def test_given_can_connect_to_workload_container_and_k8s_resources_are_applied_when_config_changed_then_config_file_is_written(  # noqa: E501
        self, _, patch_push, __, patch_k8s_get, ___, ____
    ):
        n3_cidr = "1.2.3.4/29"
        n3_gateway = "5.6.7.1"
        n4_cidr = "1.2.3.5/29"
        n4_gateway = "5.6.7.2"
        n6_cidr = "1.2.3.6/29"
        n6_gateway = "5.6.7.3"
        multus_annotation = [
            {
                "name": "n3network-free5gc-v1-free5gc-upf",
                "interface": "n3",
                "ips": [n3_cidr],
                "gateway": [n3_gateway],
            },
            {
                "name": "n6network-free5gc-v1-free5gc-upf",
                "interface": "n6",
                "ips": [n6_cidr],
                "gateway": [n6_gateway],
            },
            {
                "name": "n4network-free5gc-v1-free5gc-upf",
                "interface": "n4",
                "ips": [n4_cidr],
                "gateway": [n4_gateway],
            },
        ]
        statefulset = StatefulSet(
            spec=StatefulSetSpec(
                selector=LabelSelector(),
                serviceName="whatever name",
                template=PodTemplateSpec(
                    metadata=ObjectMeta(
                        annotations={"k8s.v1.cni.cncf.io/networks": json.dumps(multus_annotation)}
                    )
                ),
            )
        )
        nad_1 = NetworkAttachmentDefinition()
        nad_2 = NetworkAttachmentDefinition()
        nad_3 = NetworkAttachmentDefinition()
        patch_k8s_get.side_effect = [
            nad_1,
            nad_2,
            nad_3,
            statefulset,
        ]

        self.harness.set_can_connect(container="free5gc-upf", val=True)

        key_values = {
            "n3-cidr": n3_cidr,
            "n3-gateway": n3_gateway,
            "n3-interface": "eth0",
            "n4-cidr": n4_cidr,
            "n4-gateway": n4_gateway,
            "n4-interface": "eth0",
            "n6-cidr": n6_cidr,
            "n6-gateway": n6_gateway,
            "n6-interface": "eth1",
        }
        self.harness.update_config(key_values=key_values)

        patch_push.assert_called_with(
            path="/free5gc/config/upfcfg.yaml",
            source=f"version: 1.0.3\ndescription: UPF initial local configuration\n\n# The listen IP and nodeID of the N4 interface on this UPF (Can't set to 0.0.0.0)\npfcp:\n  addr: {n4_cidr.split('/')[0]}   # IP addr for listening\n  nodeID: {n4_cidr.split('/')[0]} # External IP or FQDN can be reached\n  retransTimeout: 1s # retransmission timeout\n  maxRetrans: 3 # the max number of retransmission\n\ngtpu:\n  forwarder: gtp5g\n  # The IP list of the N3/N9 interfaces on this UPF\n  # If there are multiple connection, set addr to 0.0.0.0 or list all the addresses\n  ifList:\n    - addr: {n3_cidr.split('/')[0]}\n      type: N3\n      # name: upf.5gc.nctu.me\n      # ifname: gtpif\ndnnList:\n- cidr: 10.1.0.0/17\n  dnn: internet\n  natifname: n6\nlogger:\n  enable: true\n  level: info\n  reportCaller: false",  # noqa: E501
        )

    @patch("lightkube.Client.patch")
    @patch("lightkube.Client.create")
    @patch("lightkube.Client.get")
    @patch("ops.model.Container.exec")
    @patch("ops.model.Container.push")
    @patch("lightkube.core.client.GenericSyncClient")
    def test_given_can_connect_to_workload_container_and_k8s_resources_are_applied_when_config_changed_pebble_plan_is_applied(  # noqa: E501
        self, _, __, ___, patch_k8s_get, ____, _____
    ):
        n3_cidr = "1.2.3.4/29"
        n3_gateway = "5.6.7.1"
        n4_cidr = "1.2.3.5/29"
        n4_gateway = "5.6.7.2"
        n6_cidr = "1.2.3.6/29"
        n6_gateway = "5.6.7.3"
        multus_annotation = [
            {
                "name": "n3network-free5gc-v1-free5gc-upf",
                "interface": "n3",
                "ips": [n3_cidr],
                "gateway": [n3_gateway],
            },
            {
                "name": "n6network-free5gc-v1-free5gc-upf",
                "interface": "n6",
                "ips": [n6_cidr],
                "gateway": [n6_gateway],
            },
            {
                "name": "n4network-free5gc-v1-free5gc-upf",
                "interface": "n4",
                "ips": [n4_cidr],
                "gateway": [n4_gateway],
            },
        ]
        statefulset = StatefulSet(
            spec=StatefulSetSpec(
                selector=LabelSelector(),
                serviceName="whatever name",
                template=PodTemplateSpec(
                    metadata=ObjectMeta(
                        annotations={"k8s.v1.cni.cncf.io/networks": json.dumps(multus_annotation)}
                    )
                ),
            )
        )
        nad_1 = NetworkAttachmentDefinition()
        nad_2 = NetworkAttachmentDefinition()
        nad_3 = NetworkAttachmentDefinition()
        patch_k8s_get.side_effect = [
            nad_1,
            nad_2,
            nad_3,
            statefulset,
        ]

        self.harness.set_can_connect(container="free5gc-upf", val=True)

        key_values = {
            "n3-cidr": n3_cidr,
            "n3-gateway": n3_gateway,
            "n3-interface": "eth0",
            "n4-cidr": n4_cidr,
            "n4-gateway": n4_gateway,
            "n4-interface": "eth0",
            "n6-cidr": n6_cidr,
            "n6-gateway": n6_gateway,
            "n6-interface": "eth1",
        }
        self.harness.update_config(key_values=key_values)

        expected_plan = {
            "services": {
                "free5gc-upf": {
                    "override": "replace",
                    "command": "upf -c /free5gc/config/upfcfg.yaml",
                    "startup": "enabled",
                }
            },
        }

        updated_plan = self.harness.get_container_pebble_plan("free5gc-upf").to_dict()

        self.assertEqual(expected_plan, updated_plan)

    @patch("lightkube.Client.delete")
    @patch("lightkube.core.client.GenericSyncClient")
    def test_given_when_on_remove_then(self, _, patch_delete):

        self.harness.charm.on.remove.emit()

        call_1 = call(
            res=NetworkAttachmentDefinition,
            name="n3network-free5gc-v1-free5gc-upf",
            namespace=self.model_name,
        )
        call_2 = call(
            res=NetworkAttachmentDefinition,
            name="n4network-free5gc-v1-free5gc-upf",
            namespace=self.model_name,
        )
        call_3 = call(
            res=NetworkAttachmentDefinition,
            name="n6network-free5gc-v1-free5gc-upf",
            namespace=self.model_name,
        )
        patch_delete.assert_has_calls(calls=[call_1, call_2, call_3])
