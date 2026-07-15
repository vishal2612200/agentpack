; JavaScript semantic-core query captures.
(function_declaration name: (identifier) @function.name) @function
(class_declaration name: (identifier) @class.name) @class
(method_definition name: (_) @method.name) @method
(call_expression function: (_) @call.function) @call
(import_statement) @import
(comment) @comment
