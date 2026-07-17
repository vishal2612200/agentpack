; Rust semantic-core query captures.
(function_item name: (identifier) @function.name) @function
(struct_item name: (type_identifier) @class.name) @class
(trait_item name: (type_identifier) @class.name) @class
(impl_item) @class
(call_expression function: (_) @call.function) @call
(use_declaration) @import
(line_comment) @comment
(block_comment) @comment
