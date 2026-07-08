; Ruby tree-sitter query used by tree_sitter_backend.
; Capture groups:
;   @class     class + module declarations (module is Ruby's namespace)
;   @function  top-level defs (post-processing decides function vs method
;              based on enclosing class/module)
;   @method    singleton methods (def self.foo)
;   @import    require / require_relative calls (load / autoload also picked up)

(class name: (constant) @class.name) @class
(module name: (constant) @class.name) @class

(method name: (identifier) @function.name) @function
(singleton_method name: (identifier) @method.name) @method

; require / require_relative call — grab the string literal path
(call
  method: (identifier) @_m
  arguments: (argument_list (string (string_content) @import.path))
  (#any-of? @_m "require" "require_relative" "load" "autoload")) @import
