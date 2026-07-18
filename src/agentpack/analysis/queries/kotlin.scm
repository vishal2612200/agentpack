; Kotlin semantic-core query captures.
(class_declaration (type_identifier) @class.name) @class
(object_declaration (type_identifier) @class.name) @class
(function_declaration (simple_identifier) @function.name) @function
(call_expression (simple_identifier) @call.function) @call
(import_header (identifier) @import.path) @import
(line_comment) @comment
(multiline_comment) @comment
