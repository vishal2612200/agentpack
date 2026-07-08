; Java tree-sitter query used by tree_sitter_backend.
; Capture groups:
;   @class     class/interface/enum/record declaration nodes
;   @function  top-level function-like declaration (rare in Java)
;   @method    method + constructor declarations
;   @import    import statement (dotted path node)

(class_declaration name: (identifier) @class.name) @class
(interface_declaration name: (identifier) @class.name) @class
(enum_declaration name: (identifier) @class.name) @class
(record_declaration name: (identifier) @class.name) @class

(method_declaration name: (identifier) @method.name) @method
(constructor_declaration name: (identifier) @method.name) @method

(import_declaration (_) @import.path) @import
