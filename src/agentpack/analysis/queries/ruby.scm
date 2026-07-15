; Ruby semantic-core query captures.
(class (constant) @class.name) @class
(module (constant) @class.name) @class
(method (identifier) @method.name) @method
(singleton_method (identifier) @method.name) @method
(call method: (identifier) @call.function) @call
(call method: (identifier) @import.name arguments: (argument_list (string) @import.path)) @import
(comment) @comment
