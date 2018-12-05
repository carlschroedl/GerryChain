import collections

from gerrychain.graph import Graph
from gerrychain.updaters import compute_edge_flows, flows_from_changes


def level_sets(mapping: dict):
    sets = collections.defaultdict(set)
    for source, target in mapping.items():
        sets[target].add(source)
    return sets


class Assignment:
    def __init__(self, parts):
        self.parts = parts

    @classmethod
    def from_dict(cls, assignment: dict):
        parts = {
            part: frozenset(nodes) for part, nodes in level_sets(assignment).items()
        }
        return cls(parts)

    def __getitem__(self, node):
        for part, nodes in self.parts.items():
            if node in nodes:
                return part
        raise KeyError(node)

    def copy(self):
        # This .copy() does not duplicate the frozensets
        return Assignment(self.parts.copy())

    def update(self, mapping: dict):
        flows = flows_from_changes(self, mapping)
        for part, flow in flows.items():
            # Union between frozenset and set returns an object whose type
            # matches the object on the left, which here is a frozenset
            self.parts[part] = (self.parts[part] - flow["out"]) | flow["in"]

    def items(self):
        for part, nodes in self.parts.items():
            for node in nodes:
                yield (node, part)

    def update_parts(self, new_parts):
        for part, nodes in new_parts.items():
            self.parts[part] = frozenset(nodes)


class Partition:
    """
    Partition represents a partition of the nodes of the graph. It will perform
    the first layer of computations at each step in the Markov chain - basic
    aggregations and calculations that we want to optimize.

    """

    default_updaters = {}

    def __init__(
        self, graph=None, assignment=None, updaters=None, parent=None, flips=None
    ):
        """
        :param graph: Underlying graph; a NetworkX object.
        :param assignment: Dictionary assigning nodes to districts. If None,
            initialized to assign all nodes to district 0.
        :param updaters: Dictionary of functions to track data about the partition.
            The keys are stored as attributes on the partition class,
            which the functions compute.
        """
        if parent:
            self._from_parent(parent, flips)
            self._update()
        else:
            self._first_time(graph, assignment, updaters)
            self._update()
            self.parts = tuple(self.parts.keys())

    def _first_time(self, graph, assignment, updaters):
        self.graph = graph

        self.assignment = get_assignment(assignment, graph)

        if updaters is None:
            updaters = dict()
        self.updaters = self.default_updaters.copy()
        self.updaters.update(updaters)

        self.parent = None
        self.flips = None
        self.flows = None
        self.edge_flows = None

        self.parts = level_sets(self.assignment)

    def _from_parent(self, parent, flips):
        self.parent = parent
        self.flips = flips

        self.assignment = parent.assignment.copy()
        self.assignment.update(flips)

        self.graph = parent.graph
        self.parts = parent.parts
        self.updaters = parent.updaters

        self.flows = flows_from_changes(parent.assignment, flips)
        self.edge_flows = compute_edge_flows(self)

    def __repr__(self):
        number_of_parts = len(self)
        s = "s" if number_of_parts > 1 else ""
        return "Partition of a graph into {} part{}".format(number_of_parts, s)

    def __len__(self):
        return len(self.parts)

    def _update(self):
        self._cache = dict()

        for key in self.updaters:
            if key not in self._cache:
                self._cache[key] = self.updaters[key](self)

    def merge(self, flips):
        """Returns the new partition obtained by performing the given `flips`
        on this partition.

        :param flips: dictionary assigning nodes of the graph to their new districts
        :return: the new :class:`Partition`
        :rtype: Partition
        """
        return self.__class__(parent=self, flips=flips)

    def crosses_parts(self, edge):
        """Answers the question "Does this edge cross from one part of the
        partition to another?

        :param edge: tuple of node IDs
        :rtype: bool
        """
        return self.assignment[edge[0]] != self.assignment[edge[1]]

    def __getitem__(self, key):
        """Allows accessing the values of updaters computed for this
        Partition instance.

        :param key: Property to access.
        """
        if key not in self._cache:
            self._cache[key] = self.updaters[key](self)
        return self._cache[key]

    @classmethod
    def from_json(cls, graph_path, assignment, updaters=None):
        """Creates a :class:`Partition` from a json file containing a
        serialized NetworkX `adjacency_data` object. Files of this
        kind for each state are available in the @gerrymandr/vtd-adjacency-graphs
        GitHub repository.

        :param graph_path: String filename for the json file
        :param assignment: String key for the node attribute giving a district
            assignment, or a dictionary mapping node IDs to district IDs.
        :param updaters: (optional) Dictionary of updater functions to
            attach to the partition, in addition to the default_updaters of `cls`.
        """
        graph = Graph.from_json(graph_path)

        return cls(graph, assignment, updaters)

    def to_json(
        self, json_path, *, save_assignment_as=None, include_geometries_as_geojson=False
    ):
        """Save the partition to a JSON file in the NetworkX json_graph format.

        :param json_file: Path to target JSON file.
        :param str save_assignment_as: (optional) The string to use as a node attribute
            key holding the current assignment. By default, does not save the
            assignment as an attribute.
        :param bool include_geometries_as_geojson: (optional) Whether to include any
            :mod:`shapely` geometry objects encountered in the graph's node attributes
            as GeoJSON. The default (``False``) behavior is to remove all geometry
            objects because they are not serializable. Including the GeoJSON will result
            in a much larger JSON file.
        """
        graph = Graph(self.graph)

        if save_assignment_as is not None:
            for node in graph.nodes:
                graph.nodes[node][save_assignment_as] = self.assignment[node]

        graph.to_json(
            json_path, include_geometries_as_geojson=include_geometries_as_geojson
        )

    @classmethod
    def from_file(cls, filename, assignment, updaters=None, columns=None):
        """Create a :class:`Partition` from an ESRI Shapefile, a GeoPackage,
        a GeoJSON file, or any other file that the `fiona` library can handle.
        """
        graph = Graph.from_file(filename, cols_to_add=columns)
        return cls(graph, assignment, updaters)


def get_assignment(assignment, graph):
    if isinstance(assignment, str):
        return Assignment.from_dict(graph.node_attribute(assignment))
    elif isinstance(assignment, dict):
        return Assignment.from_dict(assignment)
    elif isinstance(assignment, Assignment):
        return assignment
    else:
        raise TypeError("Assignment must be a dict or a node attribute key")
