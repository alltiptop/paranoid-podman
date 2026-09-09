"""Optional safe YAML loader with duplicate-key rejection."""

from __future__ import annotations

from typing import Any

try:
    import yaml as yaml
except ImportError:  # pragma: no cover - exercised on systems without PyYAML
    yaml = None  # type: ignore[assignment]  # Optional module, checked before use.


UniqueKeyLoader: type[yaml.SafeLoader] | None
if yaml is not None:

    class _UniqueKeyLoader(yaml.SafeLoader):
        """Safe YAML loader that also rejects duplicate mapping keys."""

    def _construct_unique_mapping(
        loader: Any, node: Any, deep: bool = False
    ) -> dict[Any, Any]:
        loader.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an invalid mapping key",
                    key_node.start_mark,
                ) from error
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found a duplicate mapping key",
                    key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    _UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        _construct_unique_mapping,
    )
    UniqueKeyLoader = _UniqueKeyLoader
else:
    UniqueKeyLoader = None
