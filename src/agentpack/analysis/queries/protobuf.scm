; Protobuf tree-sitter query used by tree_sitter_backend.
; Capture groups:
;   @class     message / service / enum declarations
;   @method    rpc declarations, qualified under the enclosing @service
;              (rpc is a direct child of service in this grammar, exactly
;              like a Java method inside a class)
;   @import    import statement's string-literal path

(message
  (message_name (identifier) @class.name)) @class

(service
  (service_name (identifier) @class.name)) @class

(enum
  (enum_name (identifier) @class.name)) @class

(rpc
  (rpc_name (identifier) @method.name)) @method

(import (string) @import.path) @import
