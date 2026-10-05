"""FastAPI Customer CRUD with a SQLite database and an OpenAPI schema."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import field_validator
from sqlmodel import Field, Session, SQLModel, create_engine, select

DB_PATH = Path(__file__).resolve().parent / "customers.db"
engine = create_engine(
    f"sqlite:///{DB_PATH.as_posix()}",
    connect_args={"check_same_thread": False},
)


class CustomerBase(SQLModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=200)
    phone: str = Field(min_length=3, max_length=40)
    city: str = Field(min_length=1, max_length=100)
    age: int = Field(ge=0, le=130)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        local, separator, domain = value.partition("@")
        if not separator or not local or "." not in domain:
            raise ValueError("invalid email")
        return value


class Customer(CustomerBase, table=True):
    id: int | None = Field(default=None, primary_key=True)


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(CustomerBase):
    pass


class CustomerRead(CustomerBase):
    id: int


def create_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_db()
    yield


app = FastAPI(
    title="Customer CRUD",
    description="Temporary API for exercising the CRUD tester.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "customer-crud",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.post("/customers", response_model=CustomerRead, status_code=201, summary="Create a customer")
def create_customer(payload: CustomerCreate, session: SessionDep) -> Customer:
    customer = Customer.model_validate(payload)
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer


@app.get("/customers", response_model=list[CustomerRead], summary="List customers")
def list_customers(
    session: SessionDep,
    city: str | None = None,
    min_age: int | None = Query(default=None, ge=0, le=130),
) -> list[Customer]:
    statement = select(Customer)
    if city:
        statement = statement.where(Customer.city == city)
    if min_age is not None:
        statement = statement.where(Customer.age >= min_age)
    return list(session.exec(statement).all())


@app.get(
    "/customers/{customer_id}",
    response_model=CustomerRead,
    responses={404: {"description": "Customer not found"}},
    summary="Get a customer",
)
def get_customer(customer_id: int, session: SessionDep) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@app.put(
    "/customers/{customer_id}",
    response_model=CustomerRead,
    responses={404: {"description": "Customer not found"}},
    summary="Update a customer",
)
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    session: SessionDep,
) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    for key, value in payload.model_dump().items():
        setattr(customer, key, value)
    session.add(customer)
    session.commit()
    session.refresh(customer)
    return customer


@app.delete(
    "/customers/{customer_id}",
    status_code=204,
    responses={404: {"description": "Customer not found"}},
    summary="Delete a customer",
)
def delete_customer(customer_id: int, session: SessionDep) -> Response:
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    session.delete(customer)
    session.commit()
    return Response(status_code=204)
