; Python semantic-core query captures.
(function_definition name: (identifier) @function.name) @function
(class_definition name: (identifier) @class.name) @class
(call function: (_) @call.function) @call
(import_statement) @import
(import_from_statement) @import
(comment) @comment
