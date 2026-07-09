; GraphQL tree-sitter query used by tree_sitter_backend.
; Capture groups:
;   @class     object/interface/enum type definitions (`type X { ... }`,
;              `interface X { ... }`, `enum X { ... }`)
;   @method    field definitions, qualified under the enclosing @class via
;              the existing scope-chain mechanism (field_definition is nested
;              inside a type's fields_definition, exactly like a Java method
;              inside a class)
;
; No @import — GraphQL SDL has no cross-file import construct in the base
; spec (schema stitching conventions vary by tool, not the grammar itself).

(object_type_definition (name) @class.name) @class

(interface_type_definition (name) @class.name) @class

(enum_type_definition (name) @class.name) @class

(field_definition (name) @method.name) @method
