; Dockerfile tree-sitter query used by tree_sitter_backend.
; Capture groups:
;   @class     named build stages (`FROM <image> AS <name>`) — the closest
;              analog to a class/scope in a Dockerfile: everything between
;              this FROM and the next belongs to the stage.
;   @variable  ARG declarations (`ARG NAME=default` or `ARG NAME`) — a
;              named, top-level build parameter.
;
; No @method/@function (Dockerfiles have no nested-scope constructs) and no
; @import (COPY --from=<stage> references a stage by name, not a file path;
; not captured this pass).

(from_instruction
  (image_alias) @class.name) @class

; ARG NAME=default has two unquoted_string children (name, then default
; value); ARG NAME (no default) has one. Anchor on the literal ARG token so
; only the name binds — without the anchor, tree-sitter also matches
; starting from the value node as if it were its own ARG's name.
(arg_instruction
  "ARG"
  .
  (unquoted_string) @variable.name) @variable
