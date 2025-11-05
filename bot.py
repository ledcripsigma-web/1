import sys
import inspect

class A:
    def __getattr__(self, name):
        return B()

class B:
    def __getattr__(self, name):
        return A()

class C(type):
    def __getattribute__(cls, name):
        if name == "x":
            return D()
        return super().__getattribute__(name)

class D(metaclass=C):
    def __getattr__(self, name):
        return self.undefined_method()

def recursive_descriptor():
    class E:
        @property
        def prop(self):
            return self.prop + 1
    return E()

def nested():
    try:
        try:
            x = A().nonexistent.another.third.fourth
            y = recursive_descriptor().prop
            z = D.x.y.z
        except Exception as e1:
            raise TypeError from e1
    except Exception as e2:
        raise ValueError from e2

def main():
    import types
    module = types.ModuleType('circular')
    sys.modules['circular'] = module
    module.self = module
    
    while True:
        try:
            nested()
        except Exception as e:
            print(f"{type(e).__name__}: {e}")
            cause = e.__cause__
            while cause:
                print(f"Caused by: {type(cause).__name__}: {cause}")
                cause = cause.__cause__

if __name__ == "__main__":
    main()
