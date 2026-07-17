; Java semantic-core query captures.
(class_declaration (identifier) @class.name) @class
(interface_declaration (identifier) @class.name) @class
(enum_declaration (identifier) @class.name) @class
(method_declaration name: (identifier) @method.name) @method
(constructor_declaration name: (identifier) @method.name) @method
(method_invocation name: (_) @call.function) @call
(import_declaration) @import
(line_comment) @comment
(block_comment) @comment
