# GPLv3 License
#
# Copyright (C) 2020 Ubisoft
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
Defines the classes and configuration that controls the data synchronizations, i.e. which types and type members
should be synchronized.

This module could be enhanced to provide multiple SynchronizedProperties to that different data is synchronized at different times
according to user preferences.

see synchronization.md
"""
import logging
from typing import Any, Dict, ItemsView, Iterable, List, Union

from bpy import types as T  # noqa

from mixer.blender_data.types import is_pointer_to
from mixer.blender_data.blenddata import collection_name_to_type

DEBUG = True
logger = logging.getLogger(__name__)


def skip_bpy_data_item(collection_name, item):
    # Never want to consider these as updated, created, removed, ...
    if collection_name == "scenes":
        if item.name == "_mixer_to_be_removed_":
            return True
    elif collection_name == "images":
        if item.source == "VIEWER":
            # "Render Result", "Viewer Node"
            return True
    return False


class Filter:
    def is_active(self):
        return True


class TypeFilter(Filter):
    """
    Filter on type or Pointer to type.

    T.SceneEEVEE wil match D.scenes[0].eevee although the later is a T.PointerProperty
    """

    def __init__(self, types: Union[Any, Iterable[Any]]):
        types = types if isinstance(types, Iterable) else [types]
        self._types: Iterable[Any] = [t.bl_rna for t in types]

    def matches(self, bl_rna_property):
        return bl_rna_property.bl_rna in self._types or any([is_pointer_to(bl_rna_property, t) for t in self._types])


class TypeFilterIn(TypeFilter):
    def apply(self, properties):
        return [p for p in properties if self.matches(p)]


class TypeFilterOut(TypeFilter):
    def apply(self, properties):
        return [p for p in properties if not self.matches(p)]


class CollectionFilterOut(TypeFilter):
    def apply(self, properties):
        # srna looks like the type inside the collection
        return [
            p
            for p in properties
            if p.bl_rna is not T.CollectionProperty.bl_rna or p.srna and p.srna.bl_rna not in self._types
        ]


class FuncFilterOut(Filter):
    pass


class NameFilter(Filter):
    def __init__(self, names: Union[Any, Iterable[str]]):
        if isinstance(names, set):
            self._names = list(names)
        elif isinstance(names, str):
            self._names = [names]
        else:
            self._names = names

    def check_unknown(self, properties):
        identifiers = [p.identifier for p in properties]
        local_exclusions = set(self._names) - _exclude_names
        unknowns = [name for name in local_exclusions if name not in identifiers]
        for unknown in unknowns:
            logger.warning(f"Internal error: Filtering unknown property {unknown}. Check spelling")


class NameFilterOut(NameFilter):
    def apply(self, properties):
        if DEBUG:
            self.check_unknown(properties)
        return [p for p in properties if p.identifier not in self._names]


class NameFilterIn(NameFilter):
    def apply(self, properties):
        if DEBUG:
            self.check_unknown(properties)
        return [p for p in properties if p.identifier in self._names]


# true class with isactive()
FilterSet = Dict[Any, Iterable[Filter]]


def bases(bl_rna):
    b = bl_rna
    while b is not None:
        yield b
        b = None if b.base is None else b.base.bl_rna
    yield None


class FilterStack:
    def __init__(self):
        self._filter_stack: List[FilterSet] = []

    def get(self, bl_rna):
        pass

    def apply(self, bl_rna, properties):
        for class_ in bases(bl_rna):
            bl_rna = None if class_ is None else class_.bl_rna
            for filter_set in self._filter_stack:
                filters = filter_set.get(bl_rna, [])
                filters = filters if isinstance(filters, Iterable) else [filters]
                for filter_ in filters:
                    properties = filter_.apply(properties)
        return properties

    def append(self, filter_set: FilterSet):
        self._filter_stack.append({None if k is None else k.bl_rna: v for k, v in filter_set.items()})


BlRna = Any
PropertyName = str
Property = Any
Properties = Dict[PropertyName, Property]


class SynchronizedProperties:
    """
    Keeps track of properties to synchronize for all types.

    Only one SynchronizedProperties is currently use, but using several contexts could let the user control what is synchronized.

    TODO Removing a plugin may cause a failure because the plugin properties are loaded in SynchronizedProperties
    and never unloaded
    """

    def __init__(self, filter_stack):
        self._properties: Dict[BlRna, Properties] = {}
        self._filter_stack: FilterStack = filter_stack
        self._unhandled_bpy_data_collection_names: List[str] = None

    def properties(self, bl_rna_property: T.Property = None, bpy_type=None) -> ItemsView:
        """
        Return the properties to synchronize for bpy_type
        """
        if (bl_rna_property is None) and (bpy_type is None):
            return []
        if (bl_rna_property is not None) and (bpy_type is not None):
            raise ValueError("Exactly one of bl_rna and bpy_type must be provided")
        if bl_rna_property is not None:
            bl_rna = bl_rna_property.bl_rna
        elif bpy_type is not None:
            bl_rna = bpy_type.bl_rna
        bl_rna_properties = self._properties.get(bl_rna)
        if bl_rna_properties is None:
            filtered_properties = self._filter_stack.apply(bl_rna, list(bl_rna.properties))
            # Differential update requires that the properties are delivered in the same order
            # as Blender delivers them
            bl_rna_properties = {p.identifier: p for p in filtered_properties}
            self._properties[bl_rna] = bl_rna_properties
        return bl_rna_properties.items()

    @property
    def unhandled_bpy_data_collection_names(self) -> List[str]:
        """
        Returns the list of bpy.data collection names not handled (synchronized) by this context
        """
        if self._unhandled_bpy_data_collection_names is None:
            handled = {item[0] for item in self.properties(bpy_type=T.BlendData)}
            self._unhandled_bpy_data_collection_names = list(collection_name_to_type.keys() - handled)

        return self._unhandled_bpy_data_collection_names


test_filter = FilterStack()

blenddata_exclude = [
    # "brushes" generates harmless warnings when EnumProperty properties are initialized with a value not in the enum
    "brushes",
    # TODO actions require to handle the circular reference between ActionGroup.channel and FCurve.group
    "actions",
    # we do not need those
    "screens",
    "window_managers",
    "workspaces",
]
"""Members of bpy.data that will be totally excluded from synchronization.

Do not exclude collections that may be a target of Object.data. It we did so, an Object.data member
would be loaded ad a DatablockProxy instead of a DatablockRefProxy
"""

_exclude_names = {
    "type_info",  # for Available (?) keyingset
    "depsgraph",  # found in Viewlayer
    "rna_type",
    "is_evaluated",
    "original",
    "users",
    "use_fake_user",
    "tag",
    "is_library_indirect",
    "library",
    "override_library",
    "preview",
    "mixer_uuid",
}
"""Names of properties that are always excluded"""

default_exclusions = {
    None: [
        TypeFilterOut(T.MeshVertex),
        # Temporary: parent and child are involved in circular reference
        TypeFilterOut(T.PoseBone),
        NameFilterOut(_exclude_names),
    ],
    T.ActionGroup: [NameFilterOut("channels")],
    T.BlendData: [NameFilterOut(blenddata_exclude), TypeFilterIn(T.CollectionProperty)],  # selected collections
    # makes a loop
    T.Bone: [NameFilterOut("parent")],
    # TODO temporary ?
    T.Collection: [NameFilterOut("all_objects")],
    T.CompositorNodeRLayers: [NameFilterOut("scene")],
    T.CurveMapPoint: [NameFilterOut("select")],
    # TODO this avoids the recursion path Node.socket , NodeSocker.Node
    # can probably be included in the readonly filter
    # TODO temporary ? Restore after foreach_get()
    T.Image: [
        NameFilterOut("pixels"),
        # meaningless to sync these, since they are handled by Image.pack() ?
        NameFilterOut(["packed_file", "packed_files"]),
    ],
    # TODO see comment in specifics.py:add_element()
    T.KeyingSets: [NameFilterOut("paths")],
    T.LayerCollection: [
        # TODO temporary
        # Scene.viewlayers[i].layer_collection.collection is Scene.collection,
        # see test_scene_viewlayer_layercollection_is_master
        NameFilterOut("collection"),
        # Seems to be a view of the master collection children
        NameFilterOut("children"),
    ],
    T.GreasePencil: [
        # Temporary while we use VRtist message for meshes. Handle the datablock for uuid
        # but do not synchronize its contents
        NameFilterIn("name")
    ],
    T.Mesh: [
        # Temporary while we use VRtist message for meshes. Handle the datablock for uuid
        # but do not synchronize its contents
        NameFilterIn("name")
    ],
    T.MeshPolygon: [NameFilterOut("area")],
    T.MeshVertex: [
        # MeshVertex.groups is updated via Object.vertex_groups
        NameFilterOut("groups")
    ],
    #
    T.Node: [
        NameFilterOut(
            [
                "internal_links",
                # cannot be written: set by shader editor
                "dimensions",
            ]
        )
    ],
    T.NodeLink: [
        # see NodeLinkProxy
        NameFilterOut(["from_node", "from_socket", "to_node", "to_socket", "is_hidden"])
    ],
    T.NodeSocket: [
        # Currently synchronize builtin shading node sockets only, so assume these attributes are
        # managed only at the Node creation
        NameFilterOut(["bl_idname", "identifier", "is_linked", "is_output", "link_limit", "name", "node", "type"])
    ],
    T.NodeTree: [
        NameFilterOut(
            [
                # read only
                "view_center",
                "name",
            ]
        )
    ],
    T.Object: [
        NameFilterOut(
            [
                # bounding box, will be computed
                "dimensions",
                # TODO triggers an error on metaballs
                #   Cannot write to '<bpy_collection[0], Object.material_slots>', attribute '' because it does not exist
                #   looks like a bpy_prop_collection and the key is and empty string
                "material_slots",
                # TODO temporary, has a seed member that makes some tests fail
                "field",
                # TODO temporary, waiting for shape_key support
                # there is a loop in active_shape_key/relative_key
                "active_shape_key",
                # temporary
                "vertex_groups",
                "bound_box",
            ]
        )
    ],
    T.RenderSettings: [
        NameFilterOut(
            # just a view of "right" and "left" from RenderSettings.views
            "stereo_views"
        )
    ],
    T.Scene: [
        NameFilterOut(
            [
                # messy in tests because setting either may reset the other to frame_start or frame_end
                # would require
                "frame_preview_start",
                "frame_preview_end",
                # just a view into the scene objects
                "objects",
                # Not required and messy: plenty of uninitialized enums, several settings, like "scuplt" are None and
                # it is unclear how to do it.
                "tool_settings",
                # TODO temporary, not implemented
                "node_tree",
                "view_layers",
                "rigidbody_world",
            ]
        ),
    ],
    T.SceneEEVEE: [
        NameFilterOut(
            [
                # Readonly, not meaningful
                "gi_cache_info"
            ]
        )
    ],
    T.SequenceEditor: [NameFilterOut(["active_strip", "sequences_all"])],
    T.ViewLayer: [
        # Not useful. Requires array insertion (to do shortly)
        NameFilterOut("freestyle_settings"),
        # A view into ViewLayer objects
        NameFilterOut("objects"),
        NameFilterOut("active_layer_collection"),
    ],
}
"""
Per-type property exclusions
"""

test_filter.append(default_exclusions)
test_properties = SynchronizedProperties(test_filter)
"""For tests"""

safe_exclusions = {}

safe_depsgraph_updates = (
    T.Camera,
    T.Collection,
    T.Image,
    # no generic sync of GreasePencil, use VRtist message
    # T.GreasePencil,
    T.Lattice,
    T.Light,
    T.Material,
    # no generic sync of Mesh, use VRtist message
    # T.Mesh
    T.MetaBall,
    T.NodeTree,
    T.Object,
    T.Scene,
    T.Sound,
    T.World,
)
"""
Datablock with a type in this list will be processed by the generic synchronization of depsgraph updates.

Add new datablock type in this list to synchronize its updates as detect by depsgraph updates.
See synchronization.md
"""

safe_filter = FilterStack()
safe_blenddata_collections = [
    "cameras",
    "collections",
    "grease_pencils",
    "images",
    "lattices",
    "lights",
    "materials",
    "meshes",
    "metaballs",
    "objects",
    "scenes",
    "sounds",
    "worlds",
]
"""
The bpy.data collections in this list are checked for creation/removal and rename by BpyBlendDiff

Add a new collection to this list to synchronize creation, remova and rename events.
"""

safe_blenddata = {T.BlendData: [NameFilterIn(safe_blenddata_collections)]}
safe_filter.append(default_exclusions)
safe_filter.append(safe_exclusions)
safe_filter.append(safe_blenddata)
safe_properties = SynchronizedProperties(safe_filter)
"""
The default context used for synchronization, that provides per-type lists of properties to synchronize
"""
