"""
This module defines Blender handlers for Mixer in generic synchronization mode
"""
from __future__ import annotations

import logging

import bpy

from mixer.blender_client import data as data_api
from mixer.blender_data.diff import BpyBlendDiff
from mixer.blender_data.filter import safe_properties


from mixer.share_data import share_data

logger = logging.getLogger(__name__)


def send_scene_data_to_server(scene, dummy):

    logger.debug(
        "send_scene_data_to_server(): skip_next_depsgraph_update %s, pending_test_update %s",
        share_data.client.skip_next_depsgraph_update,
        share_data.pending_test_update,
    )

    depsgraph = bpy.context.evaluated_depsgraph_get()
    if depsgraph.updates:
        logger.debug("Current dg updates ...")
        for update in depsgraph.updates:
            logger.debug(" ......%s", update.id.original)

    # prevent processing self events, but always process test updates
    if not share_data.pending_test_update and share_data.client.skip_next_depsgraph_update:
        share_data.client.skip_next_depsgraph_update = False
        logger.debug("send_scene_data_to_server canceled (skip_next_depsgraph_update = True) ...")
        return

    share_data.pending_test_update = False
    bpy_data_proxy = share_data.bpy_data_proxy
    depsgraph = bpy.context.evaluated_depsgraph_get()

    # Delay the update of Object data to avoid Mesh updates in edit mode, but keep other updates.
    # Mesh separate delivers Collection as well as created Object and Mesh updates while the edited
    # object is in edit mode, and these updates are not delivered when leaving edit mode, so
    # make sure to process them anyway. It is also possible to edit multiple objects at once
    selected_objects = getattr(bpy.context, "selected_objects", {})
    updates = {update.id.original for update in depsgraph.updates}
    delayed_updates = set()
    for datablock in updates:
        if datablock in selected_objects and datablock.mode != "OBJECT" and datablock.data is not None:
            delayed_updates.add(datablock)
            delayed_updates.add(datablock.data)
    updates -= delayed_updates

    # delayed update processing is delayed until the selected objects return to OBJECT mode
    process_delayed_updates = not delayed_updates

    if delayed_updates:
        bpy_data_proxy.append_delayed_updates(delayed_updates)
        logger.info("send_scene_data_to_server. Delaying updates ")
        for update in delayed_updates:
            logger.info("... %s", update)

    # Compute the difference between the proxy state and the Blender state
    # It is a coarse difference at the ID level(created, removed, renamed)
    diff = BpyBlendDiff()
    diff.diff(bpy_data_proxy, safe_properties)

    # Ask the proxy to compute the list of elements to synchronize and update itself
    changeset = bpy_data_proxy.update(diff, updates, process_delayed_updates, safe_properties)

    # Send creations before update so that collection updates for new object have a valid target
    data_api.send_data_creations(changeset.creations)
    data_api.send_data_removals(changeset.removals)
    data_api.send_data_renames(changeset.renames)
    data_api.send_data_updates(changeset.updates)

    logger.debug("send_scene_data_to_server: end")
