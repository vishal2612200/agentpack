; Terraform tree-sitter query used by tree_sitter_backend.
; Capture groups:
;   @class       resource/module/data/variable/output/provider blocks.
;                Terraform's grammar has no distinct node type per block
;                kind — every block is a generic `block` node with an
;                `identifier` (the block type, e.g. "resource") followed by
;                zero or more `string_lit` labels (e.g. "aws_instance",
;                "web"). tree_sitter_backend.py joins class.name +
;                class.label(s) into one symbol name: resource.aws_instance.web
;
; No @method/@function — Terraform blocks are flat, not nested class-in-class.
; No @import — `module { source = "./x" }` is a real cross-file reference but
; deliberately not captured this pass (see PR discussion: import resolution
; without graph-IDF weighting regressed on dense graphs elsewhere; revisit
; once that's addressed).

(block
  (identifier) @class.name
  (string_lit (template_literal) @class.label)*) @class
